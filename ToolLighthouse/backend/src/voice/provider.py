"""
Tipos comunes de la capa de voz: resultado de audio, errores con codigo HTTP y el
protocolo del proveedor TTS. Ningun mensaje publico incluye detalles internos.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Protocol, runtime_checkable


@dataclass(frozen=True)
class ProviderAudio:
    audio: bytes
    mime: str


@dataclass(frozen=True)
class AudioResult:
    """Audio listo para entregar + metadatos NO sensibles."""

    audio: bytes
    mime: str
    model: str
    class_name: Optional[str] = None
    cached: bool = False
    synth_ms: Optional[float] = None


@runtime_checkable
class TTSProvider(Protocol):
    async def synthesize(self, text: str) -> ProviderAudio: ...

    async def close(self) -> None: ...


class VoiceError(Exception):
    """Base: cada subclase fija el estado HTTP y un codigo estable para el cliente."""

    status_code = 500
    code = "voice_error"
    public_message = "Error interno de voz."

    def __init__(self, message: Optional[str] = None, *, retry_after: Optional[int] = None):
        super().__init__(message or self.public_message)
        self.message = message or self.public_message
        self.retry_after = retry_after


class InvalidEventError(VoiceError):
    status_code, code, public_message = 400, "invalid_request", "Peticion invalida."


class UnauthorizedError(VoiceError):
    status_code, code, public_message = 401, "unauthorized", "Falta la credencial del backend."


class ForbiddenError(VoiceError):
    status_code, code, public_message = 403, "forbidden", "Credencial no valida."


class UnknownHintError(VoiceError):
    status_code, code, public_message = 404, "unknown_hint", "El aviso no existe o ya caduco."


class HintsUnavailableError(VoiceError):
    status_code, code, public_message = 503, "hints_disabled", "Los avisos estan deshabilitados."


class StaleEventError(VoiceError):
    status_code, code, public_message = 409, "stale_event", "El evento fue reemplazado por uno mas reciente."


class PayloadTooLargeError(VoiceError):
    status_code, code, public_message = 413, "payload_too_large", "Peticion demasiado grande."


class RateLimitedError(VoiceError):
    status_code, code, public_message = 429, "rate_limited", "Demasiados anuncios seguidos."


class QueueFullError(VoiceError):
    status_code, code, public_message = 429, "queue_full", "Cola de voz llena."


class VoiceDisabledError(VoiceError):
    status_code, code, public_message = 503, "voice_disabled", "La voz esta deshabilitada o no disponible."


class ProviderError(VoiceError):
    status_code, code, public_message = 502, "provider_error", "El proveedor de voz fallo."


class ProviderTransientError(ProviderError):
    """Fallo transitorio (red / 5xx): el bridge puede reintentar una vez."""


class ProviderTimeoutError(VoiceError):
    status_code, code, public_message = 504, "provider_timeout", "El proveedor de voz tardo demasiado."
