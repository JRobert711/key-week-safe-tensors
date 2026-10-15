"""
API de avisos: WebSocket /ws/hints y POST /hints/ticket (router FastAPI) + ciclo de vida.

Flujo con autenticacion (cuando hay X-Backend-Key / HINT_BACKEND_KEY / VOICE_BACKEND_KEY):
  1. El servidor de Next llama POST /hints/ticket con la cabecera X-Backend-Key  -> {"ticket", "expires_in"}
  2. El navegador abre  wss://<backend>/ws/hints?ticket=<ticket>
     El ticket es de un solo uso y caduca en WS_TICKET_TTL_SECONDS (30 s por defecto).
Sin credencial configurada el backend queda abierto (aceptable solo en 127.0.0.1; se avisa al arrancar):
el ticket se ignora, aunque el endpoint sigue emitiendolos para que el cliente use el mismo flujo.

POST /hints/feedback  (Fase 5, "No es eso"; misma X-Backend-Key que /hints/ticket)
  cuerpo {"hint_id": str, "verdict": "false_positive"}  -> {"ok": true, "already": bool, "muted_seconds": n}
  La clase y la confianza las toma el backend de su registro de avisos recientes, nunca del cliente.
  404 unknown_hint (no existe o caduco), 400 invalid_feedback / invalid_verdict, 413 payload_too_large,
  429 rate_limited (+Retry-After), 503 feedback_disabled. Idempotente por hint_id.

El WebSocket es solo servidor -> cliente. Mensajes: ver hub.py. Codigos de cierre (tras aceptar):
  4401 ticket ausente, invalido, usado o caducado
  4403 origen (cabecera Origin) no permitido
  4503 avisos deshabilitados en el servidor
  1013 demasiados clientes conectados (reintentar mas tarde)
  1009 el cliente envio un mensaje demasiado grande
  1001 el servidor se apaga
Errores HTTP: {"error": {"code": str, "message": str}} con Cache-Control: no-store.
El keepalive usa el ping nativo de uvicorn (ws_ping_interval/ws_ping_timeout, ver server.py).
"""

from __future__ import annotations

import asyncio
import hmac
import json
import logging
from typing import Optional

from fastapi import APIRouter, FastAPI, Request, WebSocket
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, StrictStr, ValidationError

from .feedback import FeedbackError, FeedbackService
from .hub import PROTOCOL_VERSION, HintHub

logger = logging.getLogger("HintAPI")

router = APIRouter()

MAX_CLIENT_MESSAGE_CHARS = 1024
MAX_FEEDBACK_BODY_BYTES = 512
HINT_ID_PATTERN = r"^[A-Za-z0-9_-]{8,64}$"

CLOSE_UNAUTHORIZED = 4401
CLOSE_FORBIDDEN_ORIGIN = 4403
CLOSE_DISABLED = 4503
CLOSE_TRY_LATER = 1013
CLOSE_TOO_BIG = 1009
CLOSE_GOING_AWAY = 1001


def _error(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(
        {"error": {"code": code, "message": message}},
        status_code=status,
        headers={"Cache-Control": "no-store"},
    )


def _hub(app: FastAPI) -> Optional[HintHub]:
    return getattr(app.state, "hint_hub", None)


class FeedbackBody(BaseModel):
    """extra='forbid': solo hint_id y verdict. Nada de clase, confianza ni texto libre."""

    model_config = ConfigDict(extra="forbid")

    hint_id: str = Field(pattern=HINT_ID_PATTERN)
    verdict: StrictStr = Field(max_length=32)


def _check_key(request: Request, expected: str) -> Optional[JSONResponse]:
    if not expected:
        return None
    provided = request.headers.get("x-backend-key")
    if provided is None:
        return _error(401, "unauthorized", "Falta la credencial.")
    if not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
        return _error(403, "forbidden", "Credencial invalida.")
    return None


async def _read_bounded_json(request: Request, max_bytes: int) -> object:
    """Lee el cuerpo sin superar max_bytes. Lanza ValueError('too_large') o ValueError('invalid')."""
    declared = request.headers.get("content-length")
    if declared and declared.isdigit() and int(declared) > max_bytes:
        raise ValueError("too_large")
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > max_bytes:
            raise ValueError("too_large")
    try:
        return json.loads(bytes(body))
    except (ValueError, UnicodeDecodeError):
        raise ValueError("invalid") from None


async def _safe_close(websocket: WebSocket, code: int, reason: str = "") -> None:
    try:
        await websocket.close(code=code, reason=reason)
    except Exception:
        pass  # ya estaba cerrado


# ---------------------------------------------------------------------- rutas
@router.post("/hints/ticket")
async def hints_ticket(request: Request) -> JSONResponse:
    hub = _hub(request.app)
    if hub is None:
        return _error(503, "hints_disabled", "Los avisos estan deshabilitados.")
    expected = hub.config.backend_key
    if expected:
        provided = request.headers.get("x-backend-key")
        if provided is None:
            return _error(401, "unauthorized", "Falta la credencial.")
        if not hmac.compare_digest(provided.encode("utf-8"), expected.encode("utf-8")):
            return _error(403, "forbidden", "Credencial invalida.")
    return JSONResponse(
        {"ticket": hub.tickets.issue(), "expires_in": int(hub.tickets.ttl_seconds)},
        headers={"Cache-Control": "no-store"},
    )


@router.post("/hints/feedback")
async def hints_feedback(request: Request) -> JSONResponse:
    service: Optional[FeedbackService] = getattr(request.app.state, "hint_feedback", None)
    hub = _hub(request.app)
    if service is None or hub is None:
        return _error(503, "feedback_disabled", "El feedback de avisos esta deshabilitado.")
    denied = _check_key(request, hub.config.backend_key)
    if denied is not None:
        return denied
    if not request.headers.get("content-type", "").lower().startswith("application/json"):
        return _error(400, "invalid_feedback", "Content-Type debe ser application/json.")

    try:
        raw = await _read_bounded_json(request, MAX_FEEDBACK_BODY_BYTES)
    except ValueError as exc:
        if str(exc) == "too_large":
            return _error(413, "payload_too_large", "El cuerpo es demasiado grande.")
        return _error(400, "invalid_feedback", "El cuerpo no es JSON valido.")
    try:
        body = FeedbackBody.model_validate(raw)
    except ValidationError:
        return _error(400, "invalid_feedback", "Cuerpo no valido: se espera {hint_id, verdict}.")

    try:
        # Escribe a disco: fuera del event loop.
        result = await run_in_threadpool(service.submit, body.hint_id, body.verdict)
    except FeedbackError as exc:
        headers = {"Cache-Control": "no-store"}
        if exc.retry_after is not None:
            headers["Retry-After"] = str(exc.retry_after)
        return JSONResponse(
            {"error": {"code": exc.code, "message": exc.message}}, status_code=exc.status_code, headers=headers
        )
    except Exception:
        logger.exception("Fallo inesperado al registrar el feedback del aviso.")
        return _error(500, "internal_error", "No se pudo registrar el reporte.")
    return JSONResponse(
        {"ok": True, "already": result.already, "muted_seconds": result.muted_seconds},
        headers={"Cache-Control": "no-store"},
    )


@router.websocket("/ws/hints")
async def ws_hints(websocket: WebSocket) -> None:
    hub = _hub(websocket.app)
    await websocket.accept()

    if hub is None:
        await _safe_close(websocket, CLOSE_DISABLED, "hints_disabled")
        return

    # 1. Origen (solo los navegadores envian Origin; sin la cabecera no hay riesgo de CSWSH).
    allowed = hub.config.ws_allowed_origins
    origin = websocket.headers.get("origin")
    if allowed and origin is not None and origin.strip().rstrip("/").lower() not in allowed:
        await _safe_close(websocket, CLOSE_FORBIDDEN_ORIGIN, "origin_not_allowed")
        return

    # 2. Ticket de un solo uso (si hay credencial configurada).
    if hub.config.backend_key and not hub.tickets.consume(websocket.query_params.get("ticket")):
        await _safe_close(websocket, CLOSE_UNAUTHORIZED, "invalid_ticket")
        return

    # 3. Capacidad.
    client = hub.add_client()
    if client is None:
        await _safe_close(websocket, CLOSE_TRY_LATER, "too_many_clients")
        return

    sender: Optional["asyncio.Task[None]"] = None
    try:
        await websocket.send_json({"v": PROTOCOL_VERSION, "type": "hello"})

        async def send_loop() -> None:
            try:
                while True:
                    message = await client.queue.get()
                    if message is None:  # apagado del servidor
                        await _safe_close(websocket, CLOSE_GOING_AWAY, "server_shutdown")
                        return
                    await websocket.send_json(message)
            except asyncio.CancelledError:
                raise
            except Exception:
                await _safe_close(websocket, 1011, "send_failed")

        sender = asyncio.create_task(send_loop(), name="hints-ws-sender")

        # El cliente no envia nada util: se lee solo para detectar la desconexion.
        while True:
            incoming = await websocket.receive()
            if incoming.get("type") == "websocket.disconnect":
                break
            payload = incoming.get("text") or incoming.get("bytes") or ""
            if len(payload) > MAX_CLIENT_MESSAGE_CHARS:
                await _safe_close(websocket, CLOSE_TOO_BIG, "message_too_big")
                break
    except Exception:
        # Desconexion abrupta u error de transporte: no es un fallo del servidor.
        pass
    finally:
        hub.remove_client(client)
        if sender is not None:
            sender.cancel()
            await asyncio.gather(sender, return_exceptions=True)


# ---------------------------------------------------------------------- ciclo de vida
async def startup_hints(app: FastAPI) -> None:
    """Crea el hub y lo suscribe al motor. NUNCA lanza: un problema aqui deja el WS apagado, no el servidor."""
    app.state.hint_hub = None
    app.state.hint_feedback = None
    engine = getattr(app.state, "hint_engine", None)
    if engine is None:
        return
    try:
        cfg = engine.config
        hub = HintHub(cfg, asyncio.get_running_loop())
        engine.subscribe(hub.publish_threadsafe)
        app.state.hint_hub = hub
        if cfg.feedback_enabled:
            app.state.hint_feedback = FeedbackService(engine, cfg)
        if cfg.save_frames:
            logger.warning("HINT_SAVE_FRAMES=true: se guardarán en disco los fotogramas de los avisos reportados.")
        if not cfg.backend_key:
            logger.warning(
                "Sin X-Backend-Key (HINT_BACKEND_KEY / VOICE_BACKEND_KEY): /ws/hints y /hints/ticket no exigen "
                "credencial. Aceptable solo con el backend en 127.0.0.1; defínela antes de exponerlo."
            )
        if not cfg.ws_allowed_origins:
            logger.info("WS_ALLOWED_ORIGINS vacío: no se valida el origen del WebSocket.")
    except Exception:
        logger.exception("No se pudo iniciar el WebSocket de avisos. El servidor sigue sin WebSocket.")
        app.state.hint_hub = None
        app.state.hint_feedback = None


async def shutdown_hints(app: FastAPI) -> None:
    hub: Optional[HintHub] = getattr(app.state, "hint_hub", None)
    if hub is None:
        return
    try:
        hub.close_all()
        await asyncio.sleep(0)  # deja que los emisores envien el cierre
    except Exception:
        logger.exception("Error al apagar el WebSocket de avisos (se ignora).")
    finally:
        app.state.hint_hub = None
        app.state.hint_feedback = None
