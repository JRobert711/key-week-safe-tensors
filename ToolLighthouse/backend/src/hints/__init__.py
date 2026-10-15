"""
ToolLighthouse - Avisos de baja confianza ("Creo haber visto ...").

Paquete aislado del detector: no importa cv2, ultralytics, FastAPI, detect.py ni serial_bridge.py.
Punto de entrada para server.py: HintConfig + HintEngine. La logica es pura y se prueba sin
camara ni modelo (reloj inyectable).
"""

from .config import HintConfig, HintConfigError
from .feedback import FeedbackError, FeedbackService
from .engine import Candidate, HintCleared, HintEmitted, HintEngine
from .messages import CLASS_NAMES, build_hint_message

__all__ = [
    "CLASS_NAMES",
    "Candidate",
    "HintCleared",
    "HintConfig",
    "HintConfigError",
    "HintEmitted",
    "HintEngine",
    "FeedbackError",
    "FeedbackService",
    "build_hint_message",
]
