"""
ToolLighthouse - Backend HTTP para el frontend Next.js

Expone POST /detect: recibe un fotograma JPEG (multipart, campo "image"), ejecuta
YOLOv8 y devuelve la mejor deteccion con el contrato que espera el frontend:

    { "class": str | null, "confidence": float | null, "detections": int }

Reutiliza la logica de detect.py (clases del proyecto y resolucion de pesos).
No toca UART ni la camara: eso sigue siendo responsabilidad de detect.py.

Ejecucion (desde la carpeta del proyecto):
    python src/server.py
    # o: uvicorn src.server:app --host 127.0.0.1 --port 8000

Variables de entorno (todas opcionales):
    WEIGHTS           ruta a un .pt (si no, misma jerarquia que detect.py)
    CONF              umbral de confianza (por defecto 0.70)
    MAX_UPLOAD_BYTES  tamano maximo de la imagen (por defecto 1048576 = 1 MiB)
    HOST / PORT       direccion de escucha (por defecto 127.0.0.1:8000)
"""

import logging
import os
import threading
from contextlib import asynccontextmanager
from typing import Any, Dict

import cv2
import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool

try:
    from detect import PROJECT_CLASSES, resolve_model_weights
except ImportError:
    from src.detect import PROJECT_CLASSES, resolve_model_weights

logger = logging.getLogger("DetectAPI")

CONF = float(os.getenv("CONF", "0.70"))
MAX_UPLOAD_BYTES = int(os.getenv("MAX_UPLOAD_BYTES", str(1_048_576)))
JPEG_MAGIC = b"\xff\xd8\xff"

# El modelo YOLO no es seguro para inferencias concurrentes: se serializa el acceso.
_infer_lock = threading.Lock()


@asynccontextmanager
async def lifespan(app: FastAPI):
    from ultralytics import YOLO

    weights_path, is_fallback = resolve_model_weights(os.getenv("WEIGHTS"))
    logger.info("Cargando modelo YOLO desde: %s", weights_path)
    model = YOLO(weights_path)

    # Calentamiento: la primera inferencia es lenta y el proxy tiene timeout de 10 s.
    model(np.zeros((480, 640, 3), dtype=np.uint8), conf=CONF, verbose=False)

    app.state.model = model
    app.state.is_fallback = is_fallback
    logger.info("Modelo listo (fallback=%s, conf>=%.2f)", is_fallback, CONF)
    yield


app = FastAPI(title="ToolLighthouse API", lifespan=lifespan)


def _class_name(model, cls_id: int, is_fallback: bool) -> str:
    """Con el modelo entrenado se usan los nombres del proyecto; con el fallback, los del modelo."""
    if not is_fallback and cls_id in PROJECT_CLASSES:
        return PROJECT_CLASSES[cls_id]
    return str(model.names.get(cls_id, f"clase_{cls_id}"))


def _infer(frame: np.ndarray) -> Dict[str, Any]:
    model = app.state.model
    with _infer_lock:
        results = model(frame, conf=CONF, verbose=False)

    boxes = results[0].boxes
    if boxes is None or len(boxes) == 0:
        return {"class": None, "confidence": None, "detections": 0}

    best = max(boxes, key=lambda b: float(b.conf[0]))
    cls_id = int(best.cls[0])
    return {
        "class": _class_name(model, cls_id, app.state.is_fallback),
        "confidence": round(float(best.conf[0]), 4),
        "detections": len(boxes),
    }


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

    # La inferencia es bloqueante: se ejecuta fuera del bucle de eventos.
    return await run_in_threadpool(_infer, frame)


if __name__ == "__main__":
    import uvicorn

    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")
    uvicorn.run(
        app,
        host=os.getenv("HOST", "127.0.0.1"),
        port=int(os.getenv("PORT", "8000")),
    )