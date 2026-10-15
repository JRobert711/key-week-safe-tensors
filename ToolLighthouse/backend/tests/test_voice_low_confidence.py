"""
Fase 3: voz de los avisos de baja confianza (POST /voice/audio con kind="low_confidence").
Usa un proveedor TTS FALSO y un HintEngine real; no necesita camara, YOLO ni Fish Audio.

    python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

ROOT = os.path.join(os.path.dirname(__file__), "..")
sys.path.insert(0, os.path.join(ROOT, "src"))

try:
    import httpx
    from fastapi import FastAPI

    from hints import Candidate, HintConfig, HintEngine  # noqa: E402
    from hints.messages import CLASS_NAMES, build_hint_message  # noqa: E402
    from voice.api import router as voice_router  # noqa: E402
    from voice.bridge import MAX_SPEAK_CHARS, VoiceBridge  # noqa: E402
    from voice.config import VoiceConfig  # noqa: E402
    from voice.provider import ProviderAudio  # noqa: E402

    AVAILABLE = True
except Exception as exc:  # pragma: no cover
    AVAILABLE = False
    SKIP_REASON = f"dependencias no disponibles: {exc}"


class FakeTTS:
    def __init__(self):
        self.texts = []

    async def synthesize(self, text):
        self.texts.append(text)
        return ProviderAudio(audio=b"ID3fake-" + text.encode("utf-8")[:16], mime="audio/mpeg")

    async def close(self):
        pass


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def _hint_config():
    return HintConfig.from_env(
        {"HINT_WINDOW": "4", "HINT_MIN_FRAMES": "2", "HINT_COOLDOWN_SECONDS": "0", "HINT_GLOBAL_COOLDOWN_SECONDS": "0"},
        conf=0.60,
    )


def _emit(engine, class_name="cautin", conf=0.4):
    """Alimenta el motor con fotogramas de la candidata hasta el PRIMER aviso y lo devuelve."""
    for _ in range(10):
        for event in engine.update(confirmed=[], candidates=[Candidate(class_name, conf)]):
            if type(event).__name__ == "HintEmitted":
                return event
    raise AssertionError("el motor no emitio el aviso")


@unittest.skipUnless(AVAILABLE, "dependencias no disponibles")
class LowConfidenceVoiceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tts = FakeTTS()
        self.clock = FakeClock()
        self.cfg = VoiceConfig(api_key="x", backend_key="secret-key", min_interval_seconds=0)
        self.bridge = VoiceBridge(self.cfg, self.tts, clock=self.clock)
        self.bridge.start()
        self.engine = HintEngine(_hint_config())

        self.app = FastAPI()
        self.app.include_router(voice_router)
        self.app.state.voice_bridge = self.bridge
        self.app.state.hint_engine = self.engine
        self.http = httpx.AsyncClient(transport=httpx.ASGITransport(app=self.app), base_url="http://test")

    async def asyncTearDown(self):
        await self.http.aclose()
        await self.bridge.shutdown()

    async def post(self, body, key="secret-key"):
        headers = {"X-Backend-Key": key} if key else {}
        return await self.http.post("/voice/audio", json=body, headers=headers)

    def body(self, hint, event_id="evt-00000001"):
        return {"kind": "low_confidence", "event_id": event_id, "hint_id": hint.hint_id}

    # ------------------------------------------------------------------ camino feliz
    async def test_habla_la_frase_exacta_del_aviso(self):
        hint = _emit(self.engine, "cautin")
        resp = await self.post(self.body(hint))
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["content-type"], "audio/mpeg")
        self.assertEqual(resp.headers["x-voice-kind"], "low_confidence")
        self.assertEqual(resp.headers["x-voice-hint-id"], hint.hint_id)
        self.assertEqual(resp.headers["cache-control"], "no-store")
        self.assertEqual(resp.headers["x-voice-cache"], "miss")
        # Lo hablado es exactamente lo que muestra el banner.
        self.assertEqual(self.tts.texts, [hint.message])
        self.assertEqual(self.tts.texts[0], build_hint_message("cautin"))

    async def test_concordancia_de_genero_en_la_voz(self):
        hint = _emit(self.engine, "bascula")
        await self.post(self.body(hint))
        self.assertIn("báscula", self.tts.texts[0])
        self.assertIn("moverla", self.tts.texts[0])

    async def test_todas_las_frases_caben_en_el_limite(self):
        for name in CLASS_NAMES:
            self.assertLessEqual(len(build_hint_message(name)), MAX_SPEAK_CHARS, name)

    async def test_segunda_clase_usa_cache_por_frase(self):
        h1 = _emit(self.engine, "cautin")
        await self.post(self.body(h1, "evt-00000001"))
        self.engine.reset()
        h2 = _emit(self.engine, "cautin")
        resp = await self.post(self.body(h2, "evt-00000002"))
        self.assertEqual(resp.headers["x-voice-cache"], "hit")
        self.assertEqual(len(self.tts.texts), 1)

    # ------------------------------------------------------------------ idempotencia
    async def test_reintento_del_mismo_evento_no_sintetiza_dos_veces(self):
        hint = _emit(self.engine)
        r1 = await self.post(self.body(hint))
        r2 = await self.post(self.body(hint))
        self.assertEqual(r1.content, r2.content)
        self.assertEqual(len(self.tts.texts), 1)

    async def test_reintento_tras_cerrarse_el_aviso_reutiliza_el_audio(self):
        hint = _emit(self.engine, "cautin")
        r1 = await self.post(self.body(hint))
        self.engine.update(confirmed=["cautin"], candidates=[])  # se confirma: el aviso se cierra
        r2 = await self.post(self.body(hint, "evt-00000009"))  # otro event_id, mismo hint_id
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r1.content, r2.content)
        self.assertEqual(len(self.tts.texts), 1)

    # ------------------------------------------------------------------ validacion
    async def test_hint_id_inexistente_da_404(self):
        resp = await self.post({"kind": "low_confidence", "event_id": "evt-00000001", "hint_id": "h_" + "0" * 16})
        self.assertEqual(resp.status_code, 404)
        self.assertEqual(resp.json()["error"]["code"], "unknown_hint")
        self.assertEqual(self.tts.texts, [])

    async def test_aviso_ya_cerrado_da_409(self):
        hint = _emit(self.engine, "cautin")
        self.engine.update(confirmed=["cautin"], candidates=[])
        resp = await self.post(self.body(hint))
        self.assertEqual(resp.status_code, 409)
        self.assertEqual(resp.json()["error"]["code"], "stale_event")
        self.assertEqual(self.tts.texts, [])

    async def test_aviso_reemplazado_da_409(self):
        first = _emit(self.engine, "cautin")
        second = _emit(self.engine, "bascula", 0.5)
        self.assertNotEqual(first.hint_id, second.hint_id)
        resp = await self.post(self.body(first))
        self.assertEqual(resp.status_code, 409)
        self.assertEqual((await self.post(self.body(second, "evt-00000002"))).status_code, 200)

    async def test_el_cliente_no_puede_inyectar_texto_ni_clase(self):
        hint = _emit(self.engine)
        for extra in ({"text": "hola"}, {"class_names": ["cautin"]}, {"class": "cautin"}):
            resp = await self.post({**self.body(hint), **extra})
            self.assertEqual(resp.status_code, 400, extra)
        self.assertEqual(self.tts.texts, [])

    async def test_cuerpo_invalido(self):
        for body in (
            {"kind": "low_confidence", "event_id": "evt-00000001"},  # falta hint_id
            {"kind": "low_confidence", "event_id": "x", "hint_id": "h_abc"},  # ids demasiado cortos
            {"kind": "otra_cosa", "event_id": "evt-00000001", "hint_id": "h_" + "0" * 16},
        ):
            resp = await self.post(body)
            self.assertEqual(resp.status_code, 400, body)

    async def test_sin_avisos_habilitados_da_503(self):
        self.app.state.hint_engine = None
        resp = await self.post({"kind": "low_confidence", "event_id": "evt-00000001", "hint_id": "h_" + "0" * 16})
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["error"]["code"], "hints_disabled")

    # ------------------------------------------------------------------ autenticacion y voz apagada
    async def test_exige_backend_key(self):
        hint = _emit(self.engine)
        self.assertEqual((await self.post(self.body(hint), key=None)).status_code, 401)
        self.assertEqual((await self.post(self.body(hint), key="mala")).status_code, 403)
        self.assertEqual(self.tts.texts, [])

    async def test_voz_apagada_en_caliente_da_503(self):
        hint = _emit(self.engine)
        self.bridge.set_enabled(False)
        resp = await self.post(self.body(hint))
        self.assertEqual(resp.status_code, 503)
        self.assertEqual(resp.json()["error"]["code"], "voice_disabled")

    # ------------------------------------------------------------------ aislamiento con detecciones
    async def test_no_interfiere_con_el_orden_de_las_detecciones(self):
        det = {"event_id": "evt-d0000001", "session_id": "session-0001", "class_names": ["cautin"], "transition_id": 5}
        self.assertEqual((await self.post(det)).status_code, 200)
        hint = _emit(self.engine, "bascula", 0.5)
        self.assertEqual((await self.post(self.body(hint))).status_code, 200)
        det2 = {**det, "event_id": "evt-d0000002", "class_names": ["bascula"], "transition_id": 6}
        self.assertEqual((await self.post(det2)).status_code, 200)

    async def test_deteccion_sin_kind_sigue_funcionando(self):
        det = {"event_id": "evt-d0000001", "session_id": "session-0001", "class_names": ["cautin", "bascula"], "transition_id": 1}
        resp = await self.post(det)
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.headers["x-voice-kind"], "detection")
        self.assertEqual(resp.headers["x-voice-transition-id"], "1")
        self.assertEqual(self.tts.texts, ["Detectados: cautín y báscula."])

    async def test_status_cuenta_las_peticiones_de_avisos(self):
        hint = _emit(self.engine)
        await self.post(self.body(hint))
        counters = self.bridge.status()["counters"]
        self.assertEqual(counters["low_confidence_requests"], 1)


if __name__ == "__main__":
    unittest.main()
