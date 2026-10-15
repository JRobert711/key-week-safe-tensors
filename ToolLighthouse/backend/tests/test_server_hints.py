"""
Integracion de /detect con avisos, usando un modelo YOLO FALSO (sin pesos ni ultralytics).
Se omite si faltan fastapi/cv2/httpx. Ejecutar desde la raiz del backend:

    python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))
os.environ.setdefault("SERIAL_MOCK", "true")

try:
    import cv2
    import numpy as np
    from fastapi.testclient import TestClient

    import server  # noqa: E402
    from hints import HintConfig, HintEmitted, HintEngine  # noqa: E402

    AVAILABLE = True
except Exception as exc:  # pragma: no cover
    AVAILABLE = False
    SKIP_REASON = f"dependencias no disponibles: {exc}"


class _Tensor:
    def __init__(self, data):
        self._data = data

    def tolist(self):
        return self._data


class _Boxes:
    def __init__(self, items):
        self.xyxyn = _Tensor([[0.1, 0.1, 0.5, 0.5] for _ in items])
        self.conf = _Tensor([c for _, c in items])
        self.cls = _Tensor([float(i) for i, _ in items])
        self._n = len(items)

    def __len__(self):
        return self._n


class FakeModel:
    """Imita a YOLO: devuelve solo las cajas con confianza > conf (filtro estricto, como NMS)."""

    names = {}

    def __init__(self, script):
        self.script = script  # lista de [(cls_id, conf), ...] por llamada
        self.calls = []

    def __call__(self, frame, conf, verbose=False):
        self.calls.append(conf)
        items = self.script[min(len(self.calls) - 1, len(self.script) - 1)]
        kept = [(i, c) for i, c in items if c > conf]

        class _Result:
            boxes = _Boxes(kept)

        return [_Result()]


def jpeg_bytes():
    ok, buf = cv2.imencode(".jpg", np.full((48, 64, 3), 128, dtype=np.uint8))
    assert ok
    return buf.tobytes()


@unittest.skipUnless(AVAILABLE, "dependencias del servidor no disponibles")
class DetectWithHintsTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.client = TestClient(server.app)  # sin lifespan: el estado se prepara a mano
        server.app.state.serial_ctrl = None
        server.app.state.serial_history = None
        server.app.state.is_fallback = False

    def tearDown(self):
        for attr in ("hint_engine", "infer_conf", "model"):
            if hasattr(server.app.state, attr):
                delattr(server.app.state, attr)

    def _setup(self, script, *, hints=True, **cfg):
        server.app.state.model = FakeModel(script)
        if hints:
            engine = HintEngine(HintConfig(**cfg))
            engine.subscribe(self.events.append)
            server.app.state.hint_engine = engine
            server.app.state.infer_conf = min(server.CONF, engine.config.low_conf)
        else:
            server.app.state.hint_engine = None
            server.app.state.infer_conf = server.CONF

    def _post(self):
        return self.client.post("/detect", files={"image": ("f.jpg", jpeg_bytes(), "image/jpeg")})

    def test_low_confidence_never_appears_in_response_but_triggers_hint(self):
        # clase 3 = cautin en PROJECT_CLASSES; 0.45 esta entre LOW_CONF y CONF
        self._setup([[(3, 0.45)]], global_cooldown_seconds=0)
        for _ in range(5):
            response = self._post()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(
                response.json(),
                {"class": None, "confidence": None, "detections": 0, "boxes": [], "model": "custom"},
            )
        hints = [e for e in self.events if isinstance(e, HintEmitted)]
        self.assertEqual(len(hints), 1)
        self.assertEqual(hints[0].class_name, "cautin")
        self.assertIn("Creo haber visto un cautín", hints[0].message)
        self.assertTrue(all(abs(c - 0.30) < 1e-9 for c in server.app.state.model.calls))  # infiere con LOW_CONF

    def test_confirmed_response_is_identical_to_the_old_contract(self):
        self._setup([[(3, 0.91), (0, 0.45)]])  # confirmada + candidata de otra clase
        body = self._post().json()
        self.assertEqual(set(body), {"class", "confidence", "detections", "boxes", "model"})
        self.assertEqual((body["class"], body["confidence"], body["detections"]), ("cautin", 0.91, 1))
        self.assertEqual(len(body["boxes"]), 1)
        self.assertEqual(set(body["boxes"][0]), {"class", "confidence", "x1", "y1", "x2", "y2"})
        for _ in range(8):
            self._post()
        self.assertEqual([e for e in self.events if isinstance(e, HintEmitted)], [])  # hay confirmada: sin aviso

    def test_hints_disabled_runs_model_at_conf_and_ignores_low_boxes(self):
        self._setup([[(3, 0.45)]], hints=False)
        body = self._post().json()
        self.assertEqual(body["detections"], 0)
        self.assertEqual(server.app.state.model.calls, [server.CONF])
        self.assertEqual(self.events, [])

    def test_engine_failure_never_breaks_detect(self):
        self._setup([[(3, 0.91)]])

        def boom(**_kwargs):
            raise RuntimeError("motor roto")

        server.app.state.hint_engine.update = boom
        response = self._post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["class"], "cautin")


if __name__ == "__main__":
    unittest.main()
