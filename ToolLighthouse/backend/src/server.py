"""
ToolLighthouse - Backend HTTP para el frontend Next.js

Expone POST /detect: recibe un fotograma JPEG (multipart, campo "image"), ejecuta
YOLOv8 y devuelve la mejor deteccion con el contrato que espera el frontend:

    {
      "class": str | null, "confidence": float | null, "detections": int,
      "boxes": [{ "class": str, "confidence": float, "x1": float, "y1": float, "x2": float, "y2": float }],
      "model": "custom" | "fallback"
    }

Las coordenadas de "boxes" estan normalizadas (0 a 1) respecto a la imagen recibida.
"boxes" va ordenado de mayor a menor confianza y se limita a MAX_BOXES.

Reutiliza la logica de detect.py (clases del proyecto y resolucion de pesos).
Integra SerialController para enviar por UART la clase estable detectada.

Ejecucion (desde la carpeta del proyecto):
    python src/server.py
    # o: uvicorn src.server:app --host 127.0.0.1 --port 8000

Variables de entorno (todas opcionales):
    WEIGHTS           ruta a un .pt (si no, misma jerarquia que detect.py)
    CONF              umbral de confianza (por defecto 0.70)
    MAX_UPLOAD_BYTES  tamano maximo de la imagen (por defecto 1048576 = 1 MiB)
    HOST / PORT       direccion de escucha (por defecto 127.0.0.1:8000)
    SERIAL_PORT       puerto UART (por defecto COM3)
    SERIAL_BAUDRATE   velocidad UART (por defecto 115200)
    SERIAL_MOCK       true para forzar simulacion sin hardware

Avisos de baja confianza ("Creo haber visto ..."), paquete src/hints. Se leen del entorno y de un
.env opcional del backend (ver .env.example); un valor invalido desactiva solo los avisos:
    HINTS_ENABLED                  true/false (por defecto true)
    LOW_CONF                       piso de la banda de baja confianza (0.30, debe ser < CONF)
    HINT_WINDOW / HINT_MIN_FRAMES  ventana de estabilidad (10 / 5)
    HINT_COOLDOWN_SECONDS          espera por clase (15)
    HINT_GLOBAL_COOLDOWN_SECONDS   espera entre cualquier par de avisos (10)
    HINT_BACKOFF_FACTOR            multiplicador de la espera por repeticion (2)
    HINT_COOLDOWN_MAX_SECONDS      tope de la espera creciente (120)
    HINT_REGISTRY_TTL_SECONDS      vigencia de un aviso para recibir feedback (600)
    HINT_FEEDBACK_ENABLED          activa POST /hints/feedback ("No es eso") (true)
    HINT_FP_MUTE_SECONDS           silencio de la clase tras un falso positivo, 0 = sin silencio (600)
    HINT_FEEDBACK_LOG              registro JSONL de reportes (data/hint_feedback.jsonl)
    HINT_SAVE_FRAMES               guardar el JPEG del aviso reportado, para reentrenar (false)
WebSocket de avisos (/ws/hints, POST /hints/ticket); requiere la libreria "websockets" (uvicorn[standard]):
    HINT_BACKEND_KEY               credencial X-Backend-Key (si falta se usa VOICE_BACKEND_KEY)
    WS_ALLOWED_ORIGINS             origenes permitidos, separados por comas (vacio = no se valida)
    WS_TICKET_TTL_SECONDS          vigencia del ticket de un solo uso (30)
    WS_MAX_CLIENTS                 clientes simultaneos (8)
El contrato de /detect no cambia: "boxes" solo contiene detecciones confirmadas (confianza > CONF).
"""

import logging
import os
import threading
from collections import Counter, deque
from contextlib import asynccontextmanager
from typing import Any, Dict, List, Optional, Tuple

import cv2
import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

try:
    from serial_bridge import SerialController
except ImportError:
    from src.serial_bridge import SerialController

try:
    from detect import PROJECT_CLASSES, resolve_model_weights
except ImportError:
    from src.detect import PROJECT_CLASSES, resolve_model_weights

logger = logging.getLogger("DetectAPI")

# Voz (Fish Audio): modulo aislado. Si no se puede importar, el servidor arranca igual sin voz.
try:
    try:
        from voice.api import router as voice_router, shutdown_voice, startup_voice
    except ImportError:
        from src.voice.api import router as voice_router, shutdown_voice, startup_voice
except Exception:  # un fallo de voz nunca debe impedir la deteccion
    logger.exception("No se pudo cargar el modulo de voz; el servidor arranca sin voz.")
    voice_router = None

    async def startup_voice(app):  # type: ignore[misc]
        return None

    async def shutdown_voice(app):  # type: ignore[misc]
        return None

# Avisos de baja confianza: modulo aislado. Si no se puede importar, el servidor arranca igual sin avisos.
try:
    try:
        from hints import Candidate as HintCandidate
        from hints import HintCleared, HintConfig, HintConfigError, HintEmitted, HintEngine
    except ImportError:
        from src.hints import Candidate as HintCandidate
        from src.hints import HintCleared, HintConfig, HintConfigError, HintEmitted, HintEngine
except Exception:  # un fallo de avisos nunca debe impedir la deteccion
    logger.exception("No se pudo cargar el modulo de avisos; el servidor arranca sin avisos.")
    HintEngine = None  # type: ignore[assignment,misc]

# WebSocket de avisos: tambien aislado. Si falla la importacion, los avisos siguen solo en el log.
try:
    try:
        from hints.api import router as hints_router, shutdown_hints, startup_hints
    except ImportError:
        from src.hints.api import router as hints_router, shutdown_hints, startup_hints
except Exception:  # un fallo del WebSocket nunca debe impedir la deteccion
    logger.exception("No se pudo cargar el WebSocket de avisos; el servidor arranca sin WebSocket.")
    hints_router = None

    async def startup_hints(app):  # type: ignore[misc]
        return None

    async def shutdown_hints(app):  # type: ignore[misc]
        return None

CONF = float(os.getenv("CONF", "0.60"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(1_048_576)))
JPEG_MAGIC = b"\xff\xd8\xff"
# El frontend descarta mas de 50 cajas; se limita aqui para no enviar datos que se tiran.
MAX_BOXES = 50
SERIAL_PORT = os.getenv("SERIAL_PORT", "COM3")
SERIAL_BAUDRATE = int(os.getenv("SERIAL_BAUDRATE", "115200"))
SERIAL_MOCK = os.getenv("SERIAL_MOCK", "false").strip().lower() in {"1", "true", "yes", "si"}
SERIAL_HISTORY_SIZE = 10
SERIAL_MIN_STABLE_FRAMES = 7

# Mapeo por nombre, no por ID YOLO: los IDs del modelo y los del bridge no
# comparten el mismo orden. Los IDs siguientes corresponden a serial_bridge.py.
SERIAL_CLASS_IDS = {
    "cautin": 0,
    "multimetro_digital": 1,
    "multimetro_analogico": 2,
    "estacion_soldadura": 3,
    "caja_sensores": 4,
    "ventilador_pasco": 5,
    "bascula": 6,
    "capacitor_variable": 7,
    "fuente_voltaje": 8,
}

# El modelo YOLO no es seguro para inferencias concurrentes: se serializa el acceso.
_infer_lock = threading.Lock()
# Mantiene en orden cada inferencia y su evento UART correspondiente.
_request_lock = threading.Lock()


@asynccontextmanager
async def _detector_lifespan(app: FastAPI):
    from ultralytics import YOLO

    weights_path, is_fallback = resolve_model_weights(os.getenv("WEIGHTS"))
    logger.info("Cargando modelo YOLO desde: %s", weights_path)
    model = YOLO(weights_path)

    # Calentamiento: la primera inferencia es lenta y el proxy tiene timeout de 10 s.
    model(np.zeros((480, 640, 3), dtype=np.uint8), conf=_infer_conf(), verbose=False)

    app.state.model = model
    app.state.is_fallback = is_fallback
    logger.info("Modelo listo (fallback=%s, conf>=%.2f)", is_fallback, CONF)
    yield


def _create_serial_controller() -> Optional[SerialController]:
    """Inicializa UART sin impedir que la API de detección arranque si falla."""
    try:
        controller = SerialController(
            port=SERIAL_PORT,
            baudrate=SERIAL_BAUDRATE,
            force_mock=SERIAL_MOCK,
        )
        logger.info(
            "SerialController listo (modo=%s, puerto=%s, baudrate=%d)",
            "MOCK" if controller.mock_mode else "UART",
            SERIAL_PORT,
            SERIAL_BAUDRATE,
        )
        return controller
    except Exception:
        logger.exception("No se pudo inicializar SerialController; la detección seguirá sin UART.")
        return None


def _close_serial_controller(controller: Optional[SerialController]) -> None:
    if controller is None:
        return
    try:
        controller.close()
    except Exception:
        logger.exception("Error al cerrar SerialController.")


def _dispatch_serial_unlocked(result: Dict[str, Any]) -> None:
    """Aplica estabilidad temporal y envía solo transiciones al bridge serial."""
    controller: Optional[SerialController] = getattr(app.state, "serial_ctrl", None)
    history = getattr(app.state, "serial_history", None)
    if controller is None or history is None:
        return

    # El modelo fallback no representa las clases oficiales del inventario.
    class_name = result.get("class") if result.get("model") == "custom" else None
    if class_name not in SERIAL_CLASS_IDS:
        class_name = None
    history.append(class_name)

    counts = Counter(history)
    stable_name = next(
        (name for name, count in counts.items()
         if name is not None and count >= SERIAL_MIN_STABLE_FRAMES),
        None,
    )
    serial_class_id = SERIAL_CLASS_IDS.get(stable_name) if stable_name is not None else None

    try:
        changed = controller.send_class_event(serial_class_id)
        if changed:
            byte_value = (controller.last_sent_byte or b"?").decode("ascii", errors="replace")
            logger.info(
                "[TRANSICION SERIAL ESTABLE] clase=%s -> byte='%s'",
                stable_name or "REPOSO",
                byte_value,
            )
    except Exception:
        # Un fallo de hardware nunca debe tumbar el endpoint de detección.
        logger.exception("No se pudo enviar el evento de detección al bridge serial.")


def _create_hint_engine() -> Optional["HintEngine"]:
    """Crea el motor de avisos; devuelve None (avisos apagados) ante cualquier problema."""
    if HintEngine is None:
        return None
    try:
        config = HintConfig.from_env(conf=CONF)
    except HintConfigError as exc:
        logger.error("Configuración de avisos inválida: %s Avisos deshabilitados; la detección sigue activa.", exc)
        return None
    except Exception:
        logger.exception("No se pudo leer la configuración de avisos. Avisos deshabilitados.")
        return None

    if not config.enabled:
        logger.info("Avisos de baja confianza deshabilitados (HINTS_ENABLED=false).")
        return None
    try:
        engine = HintEngine(config)
        engine.subscribe(_log_hint_event)
        if config.feedback_enabled and config.save_frames:
            engine.subscribe(_remember_hint_frame)
    except Exception:
        logger.exception("No se pudo iniciar el motor de avisos. Avisos deshabilitados.")
        return None
    logger.info("Avisos de baja confianza activos: %s", config.summary())
    return engine


def _log_hint_event(event: Any) -> None:
    # Fase 1: los avisos solo se registran en el log. El WebSocket (Fase 2) se suscribe al mismo motor.
    if isinstance(event, HintEmitted):
        logger.info(
            "[AVISO BAJA CONFIANZA] id=%s clase=%s conf=%.2f -> %s",
            event.hint_id,
            event.class_name,
            event.confidence,
            event.message,
        )
    elif isinstance(event, HintCleared):
        logger.info("[AVISO CERRADO] id=%s clase=%s motivo=%s", event.hint_id, event.class_name, event.reason)


# Fotograma que se esta procesando (solo valido dentro de _request_lock, mismo hilo que el motor de avisos).
_current_frame: Optional[np.ndarray] = None


def _remember_hint_frame(event: Any) -> None:
    """HINT_SAVE_FRAMES: guarda en memoria el JPEG del aviso recien emitido; a disco solo si lo reportan."""
    if not isinstance(event, HintEmitted) or _current_frame is None:
        return
    service = getattr(app.state, "hint_feedback", None)
    if service is None or not service.wants_frames:
        return
    ok, buf = cv2.imencode(".jpg", _current_frame, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
    if ok:
        service.remember_frame(event.hint_id, buf.tobytes())


def _infer_conf() -> float:
    """Umbral con el que se ejecuta el modelo: LOW_CONF si hay avisos activos, si no CONF."""
    return float(getattr(app.state, "infer_conf", CONF))


def _dispatch_hints_unlocked(result: Dict[str, Any], candidates: List[Dict[str, Any]]) -> None:
    """Alimenta el motor de avisos. Un fallo aquí nunca afecta a la detección ni al UART."""
    engine = getattr(app.state, "hint_engine", None)
    if engine is None:
        return
    try:
        engine.update(
            confirmed=[box["class"] for box in result.get("boxes", [])],
            candidates=[HintCandidate(c["class"], c["confidence"]) for c in candidates],
            usable=result.get("model") == "custom",
        )
    except Exception:
        logger.exception("No se pudo actualizar el motor de avisos.")


def _infer_and_dispatch(frame: np.ndarray) -> Dict[str, Any]:
    """Serializa inferencia + actualización de historial para conservar el orden de frames."""
    global _current_frame
    with _request_lock:
        result, candidates = _infer_full(frame)
        _dispatch_serial_unlocked(result)
        _current_frame = frame
        try:
            _dispatch_hints_unlocked(result, candidates)
        finally:
            _current_frame = None
        return result


@asynccontextmanager
async def lifespan(app: FastAPI):
    # La voz se levanta y se apaga alrededor del detector; sus fallos no se propagan.
    await startup_voice(app)
    app.state.serial_ctrl = await run_in_threadpool(_create_serial_controller)
    app.state.serial_history = deque(maxlen=SERIAL_HISTORY_SIZE)
    # El motor debe existir antes de cargar el modelo: de él depende el umbral de inferencia.
    app.state.hint_engine = _create_hint_engine()
    engine = app.state.hint_engine
    app.state.infer_conf = min(CONF, engine.config.low_conf) if engine is not None else CONF
    await startup_hints(app)  # suscribe el WebSocket al motor; nunca lanza
    try:
        async with _detector_lifespan(app):
            yield
    finally:
        await run_in_threadpool(_close_serial_controller, app.state.serial_ctrl)
        app.state.serial_ctrl = None
        await shutdown_hints(app)
        app.state.hint_engine = None
        await shutdown_voice(app)


app = FastAPI(title="ToolLighthouse API", lifespan=lifespan)
if voice_router is not None:
    app.include_router(voice_router)
if hints_router is not None:
    app.include_router(hints_router)


def _class_name(model, cls_id: int, is_fallback: bool) -> str:
    """Con el modelo entrenado se usan los nombres del proyecto; con el fallback, los del modelo."""
    if not is_fallback and cls_id in PROJECT_CLASSES:
        return PROJECT_CLASSES[cls_id]
    return str(model.names.get(cls_id, f"clase_{cls_id}"))


def _infer_full(frame: np.ndarray) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    """
    Ejecuta el modelo y separa dos bandas:
      - confirmadas (confianza > CONF): forman la respuesta de /detect, igual que antes
      - candidatas  (LOW_CONF < confianza <= CONF): solo alimentan los avisos, nunca salen en /detect
    Las confirmadas son las mismas con o sin LOW_CONF: NMS procesa de mayor a menor confianza, así que
    una caja de baja confianza no puede suprimir una de alta.
    """
    model = app.state.model
    is_fallback = app.state.is_fallback
    model_kind = "fallback" if is_fallback else "custom"

    with _infer_lock:
        results = model(frame, conf=_infer_conf(), verbose=False)

    boxes = results[0].boxes
    if boxes is None or len(boxes) == 0:
        return {"class": None, "confidence": None, "detections": 0, "boxes": [], "model": model_kind}, []

    # xyxyn: esquinas normalizadas (0 a 1) respecto a la imagen decodificada.
    coords = boxes.xyxyn.tolist()
    confs = boxes.conf.tolist()
    class_ids = [int(c) for c in boxes.cls.tolist()]

    confirmed: List[Dict[str, Any]] = []
    candidates: List[Dict[str, Any]] = []
    for cls_id, conf, (x1, y1, x2, y2) in zip(class_ids, confs, coords):
        item = {
            "class": _class_name(model, cls_id, is_fallback),
            "confidence": round(float(conf), 4),
            "x1": round(float(x1), 4),
            "y1": round(float(y1), 4),
            "x2": round(float(x2), 4),
            "y2": round(float(y2), 4),
        }
        # La comparación usa la confianza sin redondear.
        (confirmed if float(conf) > CONF else candidates).append(item)

    confirmed.sort(key=lambda d: d["confidence"], reverse=True)
    if not confirmed:
        return {"class": None, "confidence": None, "detections": 0, "boxes": [], "model": model_kind}, candidates

    best = confirmed[0]
    result = {
        "class": best["class"],
        "confidence": best["confidence"],
        "detections": len(confirmed),
        "boxes": confirmed[:MAX_BOXES],
        "model": model_kind,
    }
    return result, candidates


def _infer(frame: np.ndarray) -> Dict[str, Any]:
    return _infer_full(frame)[0]


@app.get("/health")
def health() -> Dict[str, Any]:
    return {"status": "ok", "fallback_model": app.state.is_fallback}


@app.post("/detect")
async def detect(image: UploadFile = File(...)) -> Dict[str, Any]:
    # Se lee un byte de mas para detectar archivos que superan el limite.
    data = await image.read(MAX_UPLOAD_BYTES + 1)
    if not data:
        raise HTTPException(status_code=400, detail="Imagen vacia.")
    if len(data) > MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="Imagen demasiado grande.")
    if not data.startswith(JPEG_MAGIC):
        raise HTTPException(status_code=400, detail="La imagen debe ser JPEG.")

    frame = cv2.imdecode(np.frombuffer(data, dtype=np.uint8), cv2.IMREAD_COLOR)
    if frame is None:
        raise HTTPException(status_code=400, detail="No se pudo decodificar la imagen.")

    # Inferencia + envío UART son bloqueantes: se ejecutan fuera del event loop.
    # La respuesta HTTP conserva exactamente el contrato anterior.
    return await run_in_threadpool(_infer_and_dispatch, frame)


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    uvicorn.run(
        app,
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
        # Keepalive del WebSocket de avisos (ping nativo); evita que tunnels/proxies cierren conexiones inactivas.
        ws_ping_interval=20,
        ws_ping_timeout=20,
    )