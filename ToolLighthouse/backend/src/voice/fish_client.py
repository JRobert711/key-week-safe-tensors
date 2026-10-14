"""
Adaptador de Fish Audio. Es el UNICO modulo que conoce el SDK (fish-audio-sdk 1.3.x).

Parametros validados en la Fase 1 (scripts/fish_audio_smoke.py):
  AsyncFishAudio(api_key=...).tts.convert(text=, config=TTSConfig(format="mp3", mp3_bitrate=128,
  reference_id=...), model=..., request_options=RequestOptions(timeout=, max_retries=0)) -> bytes

El SDK no reintenta (max_retries=0): el reintento acotado vive en VoiceBridge.
Mapeo de errores (el detalle va al log como tipo + estado HTTP; nunca cabeceras ni la clave):
  RateLimitError                      -> RateLimitedError        (429)
  ServerError, errores de red         -> ProviderTransientError  (502, reintentable 1 vez)
  httpx.TimeoutException              -> ProviderTimeoutError    (504)
  AuthenticationError / Permission    -> ProviderError           (502; credencial propia mala)
  cualquier otro error / audio malo   -> ProviderError           (502)
"""

from __future__ import annotations

import asyncio
import logging
from typing import Optional

import httpx
from fishaudio import AsyncFishAudio
from fishaudio.core.request_options import RequestOptions
from fishaudio.exceptions import (
    APIError,
    AuthenticationError,
    FishAudioError,
    PermissionError as FishPermissionError,
    RateLimitError,
    ServerError,
)
from fishaudio.types import TTSConfig

from .provider import (
    ProviderAudio,
    ProviderError,
    ProviderTimeoutError,
    ProviderTransientError,
    RateLimitedError,
)

logger = logging.getLogger("VoiceFish")

MP3_MIME = "audio/mpeg"
MP3_BITRATE = 128


def looks_like_mp3(data: bytes) -> bool:
    """ID3v2 al inicio, o sincronizacion de frame MPEG (11 bits a 1)."""
    if data[:3] == b"ID3":
        return True
    return len(data) > 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0


def _describe(exc: BaseException) -> str:
    status = getattr(exc, "status", None)
    return type(exc).__name__ + (f" (HTTP {status})" if status else "")


class FishAudioClient:
    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        reference_id: Optional[str],
        timeout_seconds: float,
        max_audio_bytes: int,
        httpx_client: Optional[httpx.AsyncClient] = None,
    ):
        self._model = model
        self._timeout = timeout_seconds
        self._max_audio_bytes = max_audio_bytes
        self._config = TTSConfig(format="mp3", mp3_bitrate=MP3_BITRATE, reference_id=reference_id)
        self._client = AsyncFishAudio(api_key=api_key, httpx_client=httpx_client)

    async def synthesize(self, text: str) -> ProviderAudio:
        try:
            audio = await self._client.tts.convert(
                text=text,
                config=self._config,
                model=self._model,
                request_options=RequestOptions(timeout=self._timeout, max_retries=0),
            )
        except asyncio.CancelledError:
            raise
        except RateLimitError as exc:
            logger.warning("Fish Audio limito la tasa: %s", _describe(exc))
            raise RateLimitedError("El proveedor de voz limito la tasa.", retry_after=5) from None
        except (AuthenticationError, FishPermissionError) as exc:
            logger.error("Fish Audio rechazo la credencial: %s (revisa FISH_API_KEY)", _describe(exc))
            raise ProviderError() from None
        except ServerError as exc:
            logger.warning("Error del servidor de Fish Audio: %s", _describe(exc))
            raise ProviderTransientError() from None
        except APIError as exc:
            logger.warning("Fish Audio respondio con error: %s", _describe(exc))
            raise ProviderError() from None
        except httpx.TimeoutException as exc:
            logger.warning("Timeout hacia Fish Audio: %s", type(exc).__name__)
            raise ProviderTimeoutError() from None
        except httpx.TransportError as exc:
            logger.warning("Fallo de red hacia Fish Audio: %s", type(exc).__name__)
            raise ProviderTransientError() from None
        except FishAudioError as exc:
            logger.warning("Error del SDK de Fish Audio: %s", _describe(exc))
            raise ProviderError() from None
        except Exception as exc:  # defensivo: nada del SDK debe escapar sin mapear
            logger.exception("Error inesperado en el cliente de Fish Audio (%s)", type(exc).__name__)
            raise ProviderError() from None

        if not isinstance(audio, (bytes, bytearray)) or len(audio) == 0:
            logger.warning("Fish Audio devolvio una respuesta vacia o no binaria (%s)", type(audio).__name__)
            raise ProviderError()
        audio = bytes(audio)
        if len(audio) > self._max_audio_bytes:
            logger.warning("Audio de Fish Audio demasiado grande (%d bytes)", len(audio))
            raise ProviderError()
        if not looks_like_mp3(audio):
            logger.warning("El audio de Fish Audio no parece MP3 (cabecera %s)", audio[:4].hex())
            raise ProviderError()
        return ProviderAudio(audio=audio, mime=MP3_MIME)

    async def close(self) -> None:
        await self._client.close()
