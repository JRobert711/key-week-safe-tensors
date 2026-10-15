"""Pruebas de la logica de avisos (sin camara, modelo ni red). Ejecutar desde la raiz del backend:

    python -m unittest discover -s tests -v
"""

import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from hints import (  # noqa: E402
    CLASS_NAMES,
    Candidate,
    HintCleared,
    HintConfig,
    HintConfigError,
    HintEmitted,
    HintEngine,
    build_hint_message,
)


class FakeClock:
    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_engine(clock=None, **overrides):
    clock = clock or FakeClock()
    cfg = HintConfig(**overrides)
    ids = iter(f"h_test_{i:04d}" for i in range(10_000))
    return HintEngine(cfg, clock=clock, wall_clock=lambda: 1_760_000_000, id_factory=lambda: next(ids)), clock


def low(name="cautin", conf=0.45):
    return [Candidate(name, conf)]


def feed(engine, clock, frames, *, candidates=None, confirmed=(), dt=1.0, usable=True):
    """Envia `frames` fotogramas iguales avanzando `dt` s entre ellos; devuelve todos los eventos."""
    out = []
    for _ in range(frames):
        out += engine.update(confirmed=confirmed, candidates=candidates or [], usable=usable)
        clock.advance(dt)
    return out


def emitted(events):
    return [e for e in events if isinstance(e, HintEmitted)]


def cleared(events):
    return [e for e in events if isinstance(e, HintCleared)]


class MessageTests(unittest.TestCase):
    def test_masculine_and_feminine_agreement(self):
        self.assertEqual(
            build_hint_message("cautin"),
            "Creo haber visto un cautín. ¿Podrías moverlo o colocarlo en otro ángulo para verlo mejor?",
        )
        self.assertEqual(
            build_hint_message("bascula"),
            "Creo haber visto una báscula. ¿Podrías moverla o colocarla en otro ángulo para verla mejor?",
        )

    def test_every_class_builds_and_unknown_is_rejected(self):
        for name in CLASS_NAMES:
            self.assertTrue(build_hint_message(name).startswith("Creo haber visto "))
        with self.assertRaises(ValueError):
            build_hint_message("tostadora")

    def test_classes_match_voice_module(self):
        try:
            from voice.message_builder import CLASS_NAMES as VOICE_CLASSES
        except Exception:  # pragma: no cover - voice requiere pydantic/fastapi
            self.skipTest("modulo de voz no importable")
        self.assertEqual(set(CLASS_NAMES), set(VOICE_CLASSES))


class ConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = HintConfig.from_env({})
        self.assertTrue(cfg.enabled)
        self.assertEqual((cfg.low_conf, cfg.window, cfg.min_frames), (0.30, 10, 5))
        self.assertEqual((cfg.cooldown_seconds, cfg.global_cooldown_seconds), (15.0, 10.0))
        self.assertEqual((cfg.backoff_factor, cfg.cooldown_max_seconds), (2.0, 120.0))

    def test_overrides(self):
        cfg = HintConfig.from_env({"LOW_CONF": "0.4", "HINT_MIN_FRAMES": "3", "HINT_COOLDOWN_SECONDS": "30"})
        self.assertEqual((cfg.low_conf, cfg.min_frames, cfg.cooldown_seconds), (0.4, 3, 30.0))

    def test_low_conf_must_be_below_conf(self):
        with self.assertRaises(HintConfigError):
            HintConfig.from_env({"LOW_CONF": "0.6"}, conf=0.60)
        with self.assertRaises(HintConfigError):
            HintConfig.from_env({"LOW_CONF": "0.8"}, conf=0.60)

    def test_invalid_values(self):
        for env in (
            {"LOW_CONF": "abc"},
            {"LOW_CONF": "0.01"},
            {"HINT_WINDOW": "2"},
            {"HINT_WINDOW": "5", "HINT_MIN_FRAMES": "6"},
            {"HINT_BACKOFF_FACTOR": "9"},
            {"HINT_COOLDOWN_SECONDS": "-1"},
            {"HINTS_ENABLED": "quizas"},
        ):
            with self.subTest(env=env), self.assertRaises(HintConfigError):
                HintConfig.from_env(env)

    def test_default_min_frames_follows_small_window(self):
        self.assertEqual(HintConfig.from_env({"HINT_WINDOW": "3"}).min_frames, 3)

    def test_disabled_skips_validation(self):
        cfg = HintConfig.from_env({"HINTS_ENABLED": "false", "LOW_CONF": "basura"})
        self.assertFalse(cfg.enabled)


class EmissionTests(unittest.TestCase):
    def test_no_hint_before_min_frames_then_one_at_the_fifth(self):
        engine, clock = make_engine()
        self.assertEqual(emitted(feed(engine, clock, 4, candidates=low())), [])
        events = feed(engine, clock, 1, candidates=low())
        self.assertEqual(len(emitted(events)), 1)
        hint = emitted(events)[0]
        self.assertEqual(hint.class_name, "cautin")
        self.assertEqual(hint.confidence, 0.45)
        self.assertEqual(hint.hint_id, "h_test_0000")
        self.assertIn("Creo haber visto un cautín", hint.message)

    def test_flicker_below_min_frames_never_emits(self):
        engine, clock = make_engine()
        events = []
        for i in range(30):
            events += engine.update(confirmed=[], candidates=low() if i % 3 == 0 else [])
            clock.advance(1)
        self.assertEqual(emitted(events), [])

    def test_confirmed_frame_blocks_candidates(self):
        engine, clock = make_engine()
        events = feed(engine, clock, 20, candidates=low(), confirmed=["bascula"])
        self.assertEqual(emitted(events), [])

    def test_fallback_model_never_announces(self):
        engine, clock = make_engine()
        self.assertEqual(emitted(feed(engine, clock, 20, candidates=low(), usable=False)), [])

    def test_unknown_class_ignored(self):
        engine, clock = make_engine()
        self.assertEqual(emitted(feed(engine, clock, 20, candidates=[Candidate("persona", 0.5)])), [])

    def test_best_candidate_per_frame_and_mean_confidence(self):
        engine, clock = make_engine()
        cands = [Candidate("cautin", 0.40), Candidate("bascula", 0.55), Candidate("cautin", 0.50)]
        hint = emitted(feed(engine, clock, 5, candidates=cands))[0]
        self.assertEqual(hint.class_name, "bascula")
        self.assertEqual(hint.confidence, 0.55)


class AntiSpamTests(unittest.TestCase):
    def test_backoff_schedule_is_wall_clock_based(self):
        # base 15, factor 2, tope 120: esperas tras cada aviso 15, 30, 60, 120, 120...
        engine, clock = make_engine()
        times = []
        for _ in range(400):
            events = engine.update(confirmed=[], candidates=low())
            if emitted(events):
                times.append(clock.now)
            clock.advance(1)
        gaps = [b - a for a, b in zip(times, times[1:])]
        self.assertEqual(gaps[:5], [15, 30, 60, 120, 120])

    def test_schedule_independent_of_frame_rate(self):
        engine, clock = make_engine()
        times = []
        for _ in range(2000):  # 10 fotogramas por segundo
            events = engine.update(confirmed=[], candidates=low())
            if emitted(events):
                times.append(round(clock.now, 1))
            clock.advance(0.1)
        gaps = [b - a for a, b in zip(times, times[1:])]
        for got, want in zip(gaps[:3], [15.0, 30.0, 60.0]):
            self.assertAlmostEqual(got, want, delta=0.15)  # tolerancia por acumulacion de 0.1 en float

    def test_cooldown_keeps_running_if_candidate_disappears_and_returns(self):
        engine, clock = make_engine()
        feed(engine, clock, 5, candidates=low())  # primer aviso
        feed(engine, clock, 3, candidates=[])  # desaparece (< ventana: sigue activo)
        clock.advance(2)
        events = feed(engine, clock, 5, candidates=low())  # reaparece a ~10 s del aviso
        self.assertEqual(emitted(events), [])

    def test_global_cooldown_between_classes(self):
        engine, clock = make_engine(global_cooldown_seconds=10, cooldown_seconds=1)
        first = emitted(feed(engine, clock, 5, candidates=low("cautin")))
        self.assertEqual(len(first), 1)
        # otra clase estable de inmediato: bloqueada por la espera global
        blocked = emitted(feed(engine, clock, 6, candidates=low("bascula")))
        self.assertEqual(blocked, [])
        later = emitted(feed(engine, clock, 8, candidates=low("bascula")))
        self.assertEqual([h.class_name for h in later], ["bascula"])

    def test_confirmation_resets_repeats_but_keeps_base_wait(self):
        engine, clock = make_engine(global_cooldown_seconds=0)
        times = []
        for _ in range(60):
            if emitted(engine.update(confirmed=[], candidates=low())):
                times.append(clock.now)
            clock.advance(1)
        self.assertEqual(len(times), 3)  # a 0, ~15, ~45 s: el tercero ya esperaba 30 s
        events = engine.update(confirmed=["cautin"], candidates=[])  # el usuario lo coloca bien
        self.assertEqual([e.reason for e in cleared(events)], ["confirmed"])
        clock.advance(1)
        nxt = None
        for _ in range(60):
            if emitted(engine.update(confirmed=[], candidates=low())):
                nxt = clock.now
                break
            clock.advance(1)
        self.assertIsNotNone(nxt)
        self.assertLess(nxt - times[-1], 31)  # espera base (15 s) + ventana, no los 60 s del backoff

    def test_zero_cooldowns_allow_back_to_back(self):
        engine, clock = make_engine(cooldown_seconds=0, global_cooldown_seconds=0)
        events = feed(engine, clock, 12, candidates=low())
        self.assertGreaterEqual(len(emitted(events)), 5)


class ClearingTests(unittest.TestCase):
    def test_cleared_when_class_disappears_from_window(self):
        engine, clock = make_engine()
        feed(engine, clock, 5, candidates=low())
        events = feed(engine, clock, 12, candidates=[])
        self.assertEqual([e.reason for e in cleared(events)], ["lost"])
        self.assertIsNone(engine.active_hint())

    def test_cleared_when_model_becomes_unusable(self):
        engine, clock = make_engine()
        feed(engine, clock, 5, candidates=low())
        events = feed(engine, clock, 1, candidates=low(), usable=False)
        self.assertEqual([e.reason for e in cleared(events)], ["model"])

    def test_superseded_by_next_hint_of_same_class(self):
        engine, clock = make_engine()
        events = []
        for _ in range(40):
            events += engine.update(confirmed=[], candidates=low())
            clock.advance(1)
        order = [type(e).__name__ + ":" + getattr(e, "reason", "") for e in events]
        self.assertEqual(order[:3], ["HintEmitted:", "HintCleared:superseded", "HintEmitted:"])


class RegistryTests(unittest.TestCase):
    def test_get_returns_recent_hint_and_expires(self):
        engine, clock = make_engine(registry_ttl_seconds=30)
        hint = emitted(feed(engine, clock, 5, candidates=low()))[0]
        self.assertEqual(engine.get(hint.hint_id).class_name, "cautin")
        self.assertIsNone(engine.get("h_inexistente"))
        clock.advance(31)
        self.assertIsNone(engine.get(hint.hint_id))

    def test_registry_is_bounded(self):
        engine, clock = make_engine(cooldown_seconds=0, global_cooldown_seconds=0, window=3, min_frames=1)
        for _ in range(200):
            engine.update(confirmed=[], candidates=low())
            clock.advance(0.01)
        self.assertLessEqual(engine.stats()["registry"], 64)
        self.assertEqual(engine.stats()["emitted"], 200)


class ListenerTests(unittest.TestCase):
    def test_failing_listener_does_not_break_others_or_update(self):
        engine, clock = make_engine()
        received = []

        def boom(_event):
            raise RuntimeError("fallo")

        engine.subscribe(boom)
        engine.subscribe(received.append)
        events = feed(engine, clock, 5, candidates=low())
        self.assertEqual(len(emitted(events)), 1)
        self.assertEqual(len(received), 1)


if __name__ == "__main__":
    unittest.main()
