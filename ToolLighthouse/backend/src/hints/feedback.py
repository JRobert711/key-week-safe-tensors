"""
FeedbackService: "No es eso" (falso positivo confirmado por la persona). Sin FastAPI ni YOLO.

El cliente solo aporta {hint_id, verdict}. La clase y la confianza salen del registro de avisos recientes
del HintEngine (con TTL), asi que no se puede inyectar texto ni reportar un aviso que no existio.

Efectos de un reporte valido:
  1. Silencio de esa clase durante fp_mute_seconds (0 = no silenciar). Las detecciones confirmadas no cambian.
  2. Una linea JSON en el registro local: {ts, hint_id, class, confidence, verdict}. El archivo esta acotado:
     al superar MAX_LOG_BYTES se rota a "<archivo>.1" (solo se conserva una generacion).
  3. Opcional (HINT_SAVE_FRAMES=true): el JPEG del aviso en "<carpeta del registro>/hint_frames/<hint_id>.jpg"
     (negativo dificil para reentrenar). Maximo MAX_SAVED_FRAMES archivos; se borran los mas antiguos.

Es idempotente por hint_id: repetir el reporte no vuelve a silenciar ni a escribir (devuelve already=True).
Tiene limite de frecuencia global (RATE_LIMIT_MAX reportes cada RATE_LIMIT_WINDOW_SECONDS).
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import OrderedDict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Deque, Optional

from .config import HintConfig
from .engine import HintEngine

logger = logging.getLogger("HintFeedback")

VERDICT_FALSE_POSITIVE = "false_positive"
VERDICTS = (VERDICT_FALSE_POSITIVE,)

MAX_LOG_BYTES = 512 * 1024
MAX_SAVED_FRAMES = 200
MAX_REMEMBERED_FRAMES = 16  # JPEG de avisos recientes en memoria (solo con save_frames)
MAX_REPORTED_IDS = 256
RATE_LIMIT_MAX = 12
RATE_LIMIT_WINDOW_SECONDS = 60.0


class FeedbackError(Exception):
    status_code = 400
    code = "invalid_feedback"
    message = "Reporte no valido."

    def __init__(self, message: Optional[str] = None, *, retry_after: Optional[int] = None):
        super().__init__(message or self.message)
        if message:
            self.message = message
        self.retry_after = retry_after


class UnknownHintFeedbackError(FeedbackError):
    status_code = 404
    code = "unknown_hint"
    message = "El aviso no existe o ya caduco."


class RateLimitedFeedbackError(FeedbackError):
    status_code = 429
    code = "rate_limited"
    message = "Demasiados reportes seguidos."


class InvalidVerdictError(FeedbackError):
    status_code = 400
    code = "invalid_verdict"
    message = "Veredicto no permitido."


@dataclass(frozen=True)
class FeedbackResult:
    hint_id: str
    class_name: str
    already: bool
    muted_seconds: float


class FeedbackService:
    def __init__(
        self,
        engine: HintEngine,
        config: HintConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
    ):
        self._engine = engine
        self._cfg = config
        self._clock = clock
        self._wall = wall_clock
        self._lock = threading.Lock()
        self._reported: "OrderedDict[str, FeedbackResult]" = OrderedDict()
        self._recent: Deque[float] = deque()
        self._frames: "OrderedDict[str, bytes]" = OrderedDict()
        self._log_path = Path(config.feedback_log)
        self._frames_dir = self._log_path.parent / "hint_frames"
        self._submitted = 0
        self._log_failures = 0

    # ------------------------------------------------------------------ fotogramas (hilo de inferencia)
    @property
    def wants_frames(self) -> bool:
        return self._cfg.save_frames

    def remember_frame(self, hint_id: str, jpeg: bytes) -> None:
        """Guarda en memoria el JPEG de un aviso recien emitido (acotado). No toca el disco."""
        if not self._cfg.save_frames or not jpeg:
            return
        with self._lock:
            self._frames[hint_id] = jpeg
            while len(self._frames) > MAX_REMEMBERED_FRAMES:
                self._frames.popitem(last=False)

    # ------------------------------------------------------------------ reporte (bloqueante: usar run_in_threadpool)
    def submit(self, hint_id: str, verdict: object) -> FeedbackResult:
        if verdict != VERDICT_FALSE_POSITIVE:
            raise InvalidVerdictError()

        with self._lock:
            previous = self._reported.get(hint_id)
            if previous is not None:
                return FeedbackResult(previous.hint_id, previous.class_name, True, previous.muted_seconds)
            self._check_rate()

        # La clase y la confianza SIEMPRE salen del registro del motor, nunca del cliente.
        hint = self._engine.get(hint_id)
        if hint is None:
            raise UnknownHintFeedbackError()

        mute = float(self._cfg.fp_mute_seconds)
        muted = self._engine.mute_class(hint.class_name, mute)
        result = FeedbackResult(hint.hint_id, hint.class_name, False, mute if muted else 0.0)

        with self._lock:
            if hint_id in self._reported:  # carrera: otro hilo lo registro entre medias
                prev = self._reported[hint_id]
                return FeedbackResult(prev.hint_id, prev.class_name, True, prev.muted_seconds)
            self._reported[hint_id] = result
            while len(self._reported) > MAX_REPORTED_IDS:
                self._reported.popitem(last=False)
            self._recent.append(self._clock())
            self._submitted += 1
            frame = self._frames.pop(hint_id, None)

        self._append_log(hint.hint_id, hint.class_name, hint.confidence, verdict)
        if frame is not None:
            self._save_frame(hint.hint_id, frame)
        logger.info(
            "[FALSO POSITIVO] id=%s clase=%s conf=%.2f silencio=%gs",
            hint.hint_id, hint.class_name, hint.confidence, result.muted_seconds,
        )
        return result

    def stats(self) -> dict:
        with self._lock:
            return {"submitted": self._submitted, "log_failures": self._log_failures}

    # ------------------------------------------------------------------ interno
    def _check_rate(self) -> None:
        now = self._clock()
        while self._recent and now - self._recent[0] > RATE_LIMIT_WINDOW_SECONDS:
            self._recent.popleft()
        if len(self._recent) >= RATE_LIMIT_MAX:
            wait = int(RATE_LIMIT_WINDOW_SECONDS - (now - self._recent[0])) + 1
            raise RateLimitedFeedbackError(retry_after=max(wait, 1))

    def _append_log(self, hint_id: str, class_name: str, confidence: float, verdict: str) -> None:
        """Una linea JSON por reporte. Un fallo de disco se registra y se ignora: el silencio ya se aplico."""
        line = json.dumps(
            {
                "ts": int(self._wall()),
                "hint_id": hint_id,
                "class": class_name,
                "confidence": confidence,
                "verdict": verdict,
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        try:
            self._log_path.parent.mkdir(parents=True, exist_ok=True)
            try:
                if self._log_path.stat().st_size >= MAX_LOG_BYTES:
                    os.replace(self._log_path, self._log_path.with_name(self._log_path.name + ".1"))
            except FileNotFoundError:
                pass
            with open(self._log_path, "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            with self._lock:
                self._log_failures += 1
            logger.exception("No se pudo escribir el registro de feedback (%s).", self._log_path)

    def _save_frame(self, hint_id: str, jpeg: bytes) -> None:
        try:
            self._frames_dir.mkdir(parents=True, exist_ok=True)
            (self._frames_dir / f"{hint_id}.jpg").write_bytes(jpeg)
            files = sorted(self._frames_dir.glob("*.jpg"), key=lambda p: p.stat().st_mtime)
            for old in files[:-MAX_SAVED_FRAMES]:
                old.unlink(missing_ok=True)
        except OSError:
            logger.exception("No se pudo guardar el fotograma del aviso %s.", hint_id)
