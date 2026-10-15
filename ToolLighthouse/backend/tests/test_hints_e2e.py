"""
Pruebas de integracion de los avisos (motor -> hub WebSocket -> feedback). Sin FastAPI, YOLO ni red.

    python -m unittest discover -s tests -v      (desde la carpeta del backend)
"""

import asyncio
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from hints import Candidate, HintCleared, HintConfig, HintConfigError, HintEmitted, HintEngine  # noqa: E402
from hints.feedback import FeedbackService  # noqa: E402
from hints.hub import HintHub, TicketStore  # noqa: E402
from hints.messages import build_hint_message  # noqa: E402


class Clock:
    def __init__(self, t: float = 1000.0):
        self.t = t

    def __call__(self) -> float:
        return self.t


def frames(engine, clock, name, n, conf=0.4, dt=1.0):
    out = []
    for _ in range(n):
        clock.t += dt
        out += engine.update(confirmed=[], candidates=[Candidate(name, conf)])
    return out


class CascadeTest(unittest.TestCase):
    def test_default_cascade_15_30_60_120(self):
        clock = Clock()
        engine = HintEngine(HintConfig.from_env({}), clock=clock)
        times = [clock.t + i for i in range(0)]
        for _ in range(400):
            clock.t += 1
            if any(isinstance(e, HintEmitted) for e in engine.update(confirmed=[], candidates=[Candidate("cautin", 0.4)])):
                times.append(clock.t)
        gaps = [round(b - a) for a, b in zip(times, times[1:])]
        self.assertEqual(gaps[:5], [15, 30, 60, 120, 120])

    def test_confirmed_frame_blocks_and_resets_repetitions(self):
        clock = Clock()
        engine = HintEngine(HintConfig.from_env({}), clock=clock)
        first = [e for e in frames(engine, clock, "bascula", 6) if isinstance(e, HintEmitted)]
        self.assertEqual(len(first), 1)
        closed = engine.update(confirmed=["bascula"], candidates=[])
        self.assertTrue(any(isinstance(e, HintCleared) and e.reason == "confirmed" for e in closed))

    def test_message_agrees_in_gender(self):
        self.assertIn("moverlo", build_hint_message("cautin"))
        self.assertIn("moverla", build_hint_message("bascula"))
        with self.assertRaises(ValueError):
            build_hint_message("no_existe")


class PerClassConfirmationTest(unittest.TestCase):
    """Caso real: multimetro confirmado (0.93) + cautin a 0.48 en el mismo fotograma."""

    def run_frames(self, engine, clock, confirmed, candidates, n):
        out = []
        for _ in range(n):
            clock.t += 1
            out += engine.update(confirmed=confirmed, candidates=candidates)
        return out

    def test_candidate_of_other_class_is_announced_while_another_is_confirmed(self):
        clock = Clock()
        engine = HintEngine(HintConfig.from_env({}), clock=clock)
        events = self.run_frames(engine, clock, ["multimetro_analogico"], [Candidate("cautin", 0.48)], 6)
        emitted = [e for e in events if isinstance(e, HintEmitted)]
        self.assertEqual([e.class_name for e in emitted], ["cautin"])
        self.assertAlmostEqual(emitted[0].confidence, 0.48)

    def test_not_announced_before_min_frames(self):
        clock = Clock()
        engine = HintEngine(HintConfig.from_env({}), clock=clock)
        events = self.run_frames(engine, clock, ["multimetro_analogico"], [Candidate("cautin", 0.48)], 4)
        self.assertEqual([e for e in events if isinstance(e, HintEmitted)], [])

    def test_candidate_of_same_class_as_confirmed_is_ignored(self):
        clock = Clock()
        engine = HintEngine(HintConfig.from_env({}), clock=clock)
        events = self.run_frames(engine, clock, ["cautin"], [Candidate("cautin", 0.40)], 30)
        self.assertEqual([e for e in events if isinstance(e, HintEmitted)], [])

    def test_class_confirmed_after_candidate_samples_is_not_announced(self):
        clock = Clock()
        engine = HintEngine(HintConfig.from_env({}), clock=clock)
        self.run_frames(engine, clock, [], [Candidate("cautin", 0.40)], 4)   # 4 muestras, aun sin avisar
        # ahora el cautin se confirma y aparece una candidata de otra clase: el cautin no debe avisarse
        events = self.run_frames(engine, clock, ["cautin"], [Candidate("bascula", 0.40)], 4)
        self.assertFalse(any(isinstance(e, HintEmitted) and e.class_name == "cautin" for e in events))

    def test_active_hint_closes_when_its_class_gets_confirmed(self):
        clock = Clock()
        engine = HintEngine(HintConfig.from_env({}), clock=clock)
        self.run_frames(engine, clock, ["multimetro_analogico"], [Candidate("cautin", 0.48)], 6)
        self.assertIsNotNone(engine.active_hint())
        closed = self.run_frames(engine, clock, ["multimetro_analogico", "cautin"], [], 1)
        self.assertTrue(any(isinstance(e, HintCleared) and e.reason == "confirmed" for e in closed))

    def test_highest_confidence_wins_among_non_confirmed(self):
        clock = Clock()
        engine = HintEngine(HintConfig.from_env({}), clock=clock)
        cands = [Candidate("cautin", 0.35), Candidate("bascula", 0.55), Candidate("fuente_voltaje", 0.45)]
        events = self.run_frames(engine, clock, ["multimetro_analogico"], cands, 6)
        self.assertEqual([e.class_name for e in events if isinstance(e, HintEmitted)], ["bascula"])


class ConfigTest(unittest.TestCase):
    def test_defaults(self):
        cfg = HintConfig.from_env({})
        self.assertTrue(cfg.enabled and cfg.feedback_enabled)
        self.assertEqual((cfg.low_conf, cfg.min_frames, cfg.window, cfg.fp_mute_seconds), (0.30, 5, 10, 600.0))
        self.assertFalse(cfg.save_frames)

    def test_invalid_values_raise(self):
        for env in (
            {"LOW_CONF": "0.8"},                         # >= CONF
            {"LOW_CONF": "abc"},
            {"HINT_WINDOW": "2"},
            {"HINT_MIN_FRAMES": "11"},
            {"HINT_BACKOFF_FACTOR": "9"},
            {"HINT_FP_MUTE_SECONDS": "-1"},
            {"HINT_SAVE_FRAMES": "quizas"},
            {"WS_ALLOWED_ORIGINS": "https://a.com/ruta"},
        ):
            with self.subTest(env=env), self.assertRaises(HintConfigError):
                HintConfig.from_env(env)

    def test_disabled_skips_validation(self):
        self.assertFalse(HintConfig.from_env({"HINTS_ENABLED": "false", "LOW_CONF": "basura"}).enabled)


class TicketTest(unittest.TestCase):
    def test_single_use_and_expiry(self):
        clock = Clock()
        store = TicketStore(30, clock=clock)
        t = store.issue()
        self.assertTrue(store.consume(t))
        self.assertFalse(store.consume(t))            # un solo uso
        t2 = store.issue()
        clock.t += 31
        self.assertFalse(store.consume(t2))           # caducado
        self.assertFalse(store.consume(None) or store.consume("inventado"))


class EndToEndTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.mkdtemp()
        self.log = os.path.join(self.tmp, "fb.jsonl")
        self.clock = Clock()
        self.cfg = HintConfig.from_env({"HINT_FEEDBACK_LOG": self.log, "WS_MAX_CLIENTS": "2"})
        self.engine = HintEngine(self.cfg, clock=self.clock)
        self.hub = HintHub(self.cfg, asyncio.get_running_loop(), clock=self.clock)
        self.engine.subscribe(self.hub.publish_threadsafe)
        self.feedback = FeedbackService(self.engine, self.cfg, clock=self.clock)

    async def test_hint_broadcast_feedback_and_mute(self):
        a, b = self.hub.add_client(), self.hub.add_client()
        self.assertIsNone(self.hub.add_client())      # WS_MAX_CLIENTS=2

        frames(self.engine, self.clock, "cautin", 6)
        await asyncio.sleep(0)
        for client in (a, b):                         # se difunde a todos
            msg = client.queue.get_nowait()
            self.assertEqual((msg["type"], msg["class"], msg["v"]), ("low_confidence", "cautin", 1))
            self.assertRegex(msg["hint_id"], r"^h_[0-9a-f]{16}$")
        hint_id = msg["hint_id"]

        # "No es eso": el backend toma la clase de su registro; todos reciben hint_cleared
        result = self.feedback.submit(hint_id, "false_positive")
        await asyncio.sleep(0)
        self.assertEqual((result.class_name, result.already), ("cautin", False))
        for client in (a, b):
            self.assertEqual(client.queue.get_nowait(), {"v": 1, "type": "hint_cleared", "hint_id": hint_id})

        # silenciada: ni con todas las esperas vencidas
        self.assertEqual([e for e in frames(self.engine, self.clock, "cautin", 200) if isinstance(e, HintEmitted)], [])
        # una confirmada sigue funcionando y no se ve afectada
        self.assertEqual(self.engine.update(confirmed=["cautin"], candidates=[]), [])

        with open(self.log, encoding="utf-8") as fh:
            rec = json.loads(fh.readline())
        self.assertEqual(set(rec), {"ts", "hint_id", "class", "confidence", "verdict"})

    async def test_slow_client_drops_oldest_and_never_blocks(self):
        slow = self.hub.add_client()
        for i in range(40):
            self.hub._broadcast({"v": 1, "type": "hint_cleared", "hint_id": f"h_{i:016d}"})
        self.assertEqual(slow.queue.qsize(), slow.queue.maxsize)
        self.assertEqual(slow.queue.get_nowait()["hint_id"], f"h_{40 - slow.queue.maxsize:016d}")

    async def test_publish_after_loop_closed_is_ignored(self):
        loop = asyncio.new_event_loop()
        loop.close()
        hub = HintHub(self.cfg, loop)
        hub.publish_threadsafe(HintEmitted("h_0123456789abcdef", "cautin", 0.4, "x", 1))  # no lanza


if __name__ == "__main__":
    unittest.main()
