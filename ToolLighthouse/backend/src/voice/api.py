"""
API de voz (router FastAPI) y funciones de ciclo de vida.

Rutas (internas del backend; el navegador solo llama al proxy de Next.js):
  POST /voice/audio    -> 200 audio/mpeg (binario, Cache-Control: no-store). Dos tipos de cuerpo JSON:
                       kind "detection" (por defecto):
                         {event_id, session_id, class_names, transition_id}
                       kind "low_confidence" (aviso "Creo haber visto ..."):
                         {kind, event_id, hint_id}
                         La frase NO viaja en la peticion: el backend la toma del aviso registrado en el
                         HintEngine (tabla cerrada), asi lo que se habla es exactamente lo que muestra el banner.
                         Solo se habla un aviso vigente: 404 unknown_hint si no existe o caduco, 409 stale_event
                         si ya se cerro (confirmado, perdido o reemplazado), 503 hints_disabled si no hay avisos.
  GET  /voice/status   contadores y estado, sin secretos

Autenticacion: si VOICE_BACKEND_KEY esta definida, se exige la cabecera X-Backend-Key
(401 si falta, 403 si no coincide). Sin la variable, el backend queda abierto: solo aceptable
mientras escuche en 127.0.0.1 (se registra una advertencia al arrancar).

Errores: siempre {"error": {"code": str, "message": str}} con cabecera Cache-Control: no-store.
No se instalan manejadores globales: /detect y /health conservan exactamente su comportamiento.
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
from typing import Literal, Optional

from fastapi import APIRouter, FastAPI, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, ValidationError

from .bridge import VoiceBridge
from .config import VoiceConfig, VoiceConfigError
from .message_builder import SUPPORTED_LANGUAGES
from .provider import (
    AudioResult,
    ForbiddenError,
    HintsUnavailableError,
    InvalidEventError,
    PayloadTooLargeError,
    StaleEventError,
    UnauthorizedError,
    UnknownHintError,
    VoiceDisabledError,
    VoiceError,
)

logger = logging.getLogger("VoiceAPI")

router = APIRouter(prefix="/voice")

MAX_BODY_BYTES = 2048
ID_PATTERN = r"^[A-Za-z0-9_-]{8,64}$"


class VoiceEvent(BaseModel):
    """extra='forbid': no se acepta texto libre ni campos desconocidos."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["detection"] = "detection"
    event_id: str = Field(pattern=ID_PATTERN)
    session_id: str = Field(pattern=ID_PATTERN)
    class_names: list[StrictStr] = Field(min_length=1, max_length=9)
    transition_id: StrictInt = Field(ge=0, le=2_147_483_647)


class LowConfidenceEvent(BaseModel):
    """Solo identifica el aviso: ni clase ni texto vienen del cliente."""

    model_config = ConfigDict(extra="forbid")

    kind: Literal["low_confidence"]
    event_id: str = Field(pattern=ID_PATTERN)
    hint_id: str = Field(pattern=ID_PATTERN)


# ---------------------------------------------------------------------- utilidades
def _error_response(exc: VoiceError) -> JSONResponse:
    headers = {"Cache-Control": "no-store"}
    if exc.retry_after is not None:
        headers["Retry-After"] = str(exc.retry_after)
    return JSONResponse(
        {"error": {"code": exc.code, "message": exc.message}},
        status_code=exc.status_code,
        headers=headers,
    )


def _get_bridge(request: Request) -> Optional[VoiceBridge]:
    return getattr(request.app.state, "voice_bridge", None)


def _check_key(request: Request, bridge: VoiceBridge) -> None:
    expected = bridge.config.backend_key
    if not expected:
        return
    provided = request.headers.get("x-backend-key")
    if provided is None:
        raise UnauthorizedError()
    if not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        raise ForbiddenError()


async def _read_json_body(request: Request) -> object:
    if not request.headers.get("content-type", "").lower().startswith("application/json"):
        raise InvalidEventError("Content-Type debe ser application/json.")
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > MAX_BODY_BYTES:
        raise PayloadTooLargeError()
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > MAX_BODY_BYTES:
            raise PayloadTooLargeError()
    try:
        return json.loads(bytes(body))
    except (ValueError, UnicodeDecodeError):
        raise InvalidEventError("El cuerpo no es JSON valido.") from None


async def _synthesize_low_confidence(request: Request, bridge: VoiceBridge, event: LowConfidenceEvent) -> AudioResult:
    engine = getattr(request.app.state, "hint_engine", None)
    if engine is None:
        raise HintsUnavailableError()

    def resolve() -> tuple[str, str]:
        hint = engine.get(event.hint_id)
        if hint is None:
            raise UnknownHintError()
        active = engine.active_hint()
        if active is None or active.hint_id != hint.hint_id:
            raise StaleEventError()  # ya confirmado, perdido o reemplazado: no tiene sentido hablarlo
        return hint.message, hint.class_name

    return await bridge.synthesize_hint(resolve, event_id=event.event_id, hint_id=event.hint_id)


# ---------------------------------------------------------------------- rutas
@router.post("/audio")
async def voice_audio(request: Request) -> Response:
    bridge = _get_bridge(request)
    try:
        if bridge is None:
            raise VoiceDisabledError()
        _check_key(request, bridge)
        payload = await _read_json_body(request)
        try:
            if isinstance(payload, dict) and payload.get("kind") == "low_confidence":
                event = LowConfidenceEvent.model_validate(payload)
            else:
                event = VoiceEvent.model_validate(payload)
        except ValidationError:
            # No se devuelve el detalle de pydantic (podria reflejar la entrada del cliente).
            raise InvalidEventError(
                "Se esperan event_id, session_id, class_names (lista de clases permitidas) y "
                "transition_id (entero >= 0), o bien kind='low_confidence' con event_id y hint_id."
            ) from None
        if isinstance(event, LowConfidenceEvent):
            result = await _synthesize_low_confidence(request, bridge, event)
        else:
            result = await bridge.synthesize_detection(
                event.class_names,
                event_id=event.event_id,
                session_id=event.session_id,
                transition_id=event.transition_id,
            )
    except VoiceError as exc:
        return _error_response(exc)
    except Exception:
        logger.exception("Error inesperado en /voice/audio")
        return _error_response(VoiceError())

    headers = {
        "Cache-Control": "no-store",
        "X-Content-Type-Options": "nosniff",
        "X-Voice-Kind": event.kind,
        "X-Voice-Event-Id": event.event_id,
        "X-Voice-Cache": "hit" if result.cached else "miss",
    }
    if isinstance(event, LowConfidenceEvent):
        headers["X-Voice-Hint-Id"] = event.hint_id
    else:
        headers["X-Voice-Transition-Id"] = str(event.transition_id)
    return Response(content=result.audio, media_type=result.mime, headers=headers)


@router.get("/status")
async def voice_status(request: Request) -> Response:
    bridge = _get_bridge(request)
    try:
        if bridge is None:
            raise VoiceDisabledError()
        _check_key(request, bridge)
    except VoiceError as exc:
        return _error_response(exc)
    return JSONResponse(bridge.status(), headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------------- ciclo de vida
async def startup_voice(app: FastAPI) -> None:
    """Crea el VoiceBridge. NUNCA lanza: un problema de voz deja la voz apagada, no el servidor."""
    app.state.voice_bridge = None
    try:
        cfg = VoiceConfig.from_env()
    except VoiceConfigError as exc:
        logger.error("Configuracion de voz invalida: %s. Voz deshabilitada; la deteccion sigue activa.", exc)
        return
    except Exception:
        logger.exception("No se pudo leer la configuracion de voz. Voz deshabilitada.")
        return

    try:
        provider = None
        reason: Optional[str] = None
        if not cfg.enabled:
            reason = "VOICE_ENABLED=false"
            logger.info("Voz deshabilitada por configuracion (VOICE_ENABLED=false).")
        elif not cfg.api_key:
            reason = "missing_api_key"
            logger.warning("FISH_API_KEY no esta configurada: voz deshabilitada; la deteccion sigue activa.")
        else:
            if cfg.language not in SUPPORTED_LANGUAGES:
                logger.warning("VOICE_LANGUAGE=%r no tiene frases; se usara 'es'.", cfg.language)
            from .fish_client import FishAudioClient  # import perezoso: aisla al SDK del arranque

            provider = FishAudioClient(
                api_key=cfg.api_key,
                model=cfg.model,
                reference_id=cfg.reference_id,
                timeout_seconds=cfg.request_timeout_seconds,
                max_audio_bytes=cfg.max_audio_bytes,
            )

        bridge = VoiceBridge(cfg, provider, disabled_reason=reason)
        bridge.start()
        app.state.voice_bridge = bridge

        if provider is not None:
            logger.info(
                "Voz lista: modelo=%s cola=%d concurrencia=%d intervalo=%.1fs auth=%s",
                cfg.model,
                cfg.max_queue_size,
                cfg.max_concurrent_synthesis,
                cfg.min_interval_seconds,
                "X-Backend-Key" if cfg.backend_key else "ninguna",
            )
            if not cfg.backend_key:
                logger.warning(
                    "VOICE_BACKEND_KEY no esta definida: /voice/* no exige credencial. "
                    "Aceptable solo con el backend en 127.0.0.1; defínela antes de exponerlo."
                )
    except Exception:
        logger.exception("Fallo al iniciar la voz. Voz deshabilitada; la deteccion sigue activa.")
        app.state.voice_bridge = None


async def shutdown_voice(app: FastAPI) -> None:
    bridge: Optional[VoiceBridge] = getattr(app.state, "voice_bridge", None)
    if bridge is None:
        return
    try:
        await asyncio.wait_for(bridge.shutdown(), timeout=5)
    except Exception:
        logger.exception("Error al apagar la voz (se ignora).")
    finally:
        app.state.voice_bridge = None
