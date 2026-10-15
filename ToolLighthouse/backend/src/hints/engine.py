"""
HintEngine: decide CUANDO avisar "Creo haber visto X" (logica pura, sin FastAPI ni YOLO).

Entrada por fotograma (update):
  confirmed   clases de las cajas confirmadas (confianza > CONF) del fotograma
  candidates  cajas de la banda de baja confianza (LOW_CONF < confianza <= CONF)
  usable      False si el modelo es el fallback: nunca se anuncia

Reglas:
  1. Una candidata se descarta si SU CLASE esta confirmada en ese fotograma (ya se ve). Las candidatas de
     otras clases siguen contando: con un multimetro confirmado y un cautin a 0.48, el cautin puede avisarse.
  2. Cada fotograma aporta como maximo UNA candidata: la de mayor confianza entre las no confirmadas.
  3. Se avisa cuando una clase aparece en >= min_frames de los ultimos `window` fotogramas.
  4. Anti-repeticion con reloj real (time.monotonic, independiente de los FPS):
       - espera global entre cualquier par de avisos (global_cooldown_seconds)
       - espera por clase que crece con cada repeticion sin confirmar:
         tras el aviso k-esimo de la clase se exige min(base * factor**(k-1), tope) segundos
       - la espera sigue corriendo aunque la candidata desaparezca y reaparezca
       - al CONFIRMARSE la clase solo se reinicia el contador de repeticiones (la espera base se mantiene)
  5. Un aviso activo se cierra (HintCleared) cuando la clase se confirma, desaparece de la ventana,
     el modelo deja de ser utilizable o lo reemplaza un aviso nuevo.

Cada aviso recibe un hint_id y queda en un registro acotado con TTL, para que el feedback futuro
(falso positivo) resuelva clase y confianza en el backend y no a partir de lo que diga el cliente.
Los eventos se entregan a los suscriptores (subscribe) fuera del lock; un suscriptor que falla no
afecta a los demas ni a la deteccion.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid
from collections import OrderedDict, deque
from dataclasses import dataclass
from typing import Callable, Deque, Dict, Iterable, List, Optional, Tuple, Union

from .config import HintConfig
from .messages import CLASS_INFO, build_hint_message

logger = logging.getLogger("HintEngine")

MAX_REGISTRY_ENTRIES = 64
_MAX_BACKOFF_EXPONENT = 30  # evita desbordes de float con factores altos


@dataclass(frozen=True)
class Candidate:
    class_name: str
    confidence: float


@dataclass(frozen=True)
class HintEmitted:
    hint_id: str
    class_name: str
    confidence: float
    message: str
    ts: int  # epoch en segundos


@dataclass(frozen=True)
class HintCleared:
    hint_id: str
    class_name: str
    reason: str  # "confirmed" | "lost" | "superseded" | "model" | "muted"


HintEvent = Union[HintEmitted, HintCleared]
Listener = Callable[[HintEvent], None]


def _default_id() -> str:
    return f"h_{uuid.uuid4().hex[:16]}"


class HintEngine:
    def __init__(
        self,
        config: HintConfig,
        *,
        clock: Callable[[], float] = time.monotonic,
        wall_clock: Callable[[], float] = time.time,
        id_factory: Callable[[], str] = _default_id,
    ):
        self._cfg = config
        self._clock = clock
        self._wall = wall_clock
        self._new_id = id_factory
        self._lock = threading.Lock()
        self._history: Deque[Optional[Candidate]] = deque(maxlen=config.window)
        self._last_emit_at: Dict[str, float] = {}
        self._emit_count: Dict[str, int] = {}
        self._last_global: Optional[float] = None
        self._muted_until: Dict[str, float] = {}
        self._active: Optional[HintEmitted] = None
        self._registry: "OrderedDict[str, Tuple[HintEmitted, float]]" = OrderedDict()
        self._listeners: List[Listener] = []
        self._emitted_total = 0

    # ------------------------------------------------------------------ publico
    @property
    def config(self) -> HintConfig:
        return self._cfg

    def subscribe(self, listener: Listener) -> None:
        with self._lock:
            self._listeners.append(listener)

    def active_hint(self) -> Optional[HintEmitted]:
        with self._lock:
            return self._active

    def get(self, hint_id: str) -> Optional[HintEmitted]:
        """Aviso reciente (dentro del TTL) o None. Pensado para validar feedback del cliente."""
        with self._lock:
            self._purge(self._clock())
            entry = self._registry.get(hint_id)
            return entry[0] if entry else None

    def mute_class(self, class_name: str, seconds: float) -> bool:
        """
        Silencia los avisos de una clase durante `seconds` (reloj monotono). No afecta a las detecciones
        confirmadas. Si el aviso activo es de esa clase se cierra (reason="muted") para que todos los
        clientes lo oculten. Con seconds <= 0 no hace nada y devuelve False.
        """
        if seconds <= 0 or class_name not in CLASS_INFO:
            return False
        events: List[HintEvent] = []
        with self._lock:
            now = self._clock()
            self._muted_until[class_name] = max(self._muted_until.get(class_name, 0.0), now + seconds)
            if self._active is not None and self._active.class_name == class_name:
                self._close_active("muted", events)
        self._notify(events)
        return True

    def muted_remaining(self, class_name: str) -> float:
        with self._lock:
            return self._muted_remaining(class_name, self._clock())

    def stats(self) -> Dict[str, int]:
        with self._lock:
            return {"emitted": self._emitted_total, "registry": len(self._registry)}

    def reset(self) -> None:
        """Vacia ventana y aviso activo (las esperas por reloj se conservan)."""
        events: List[HintEvent] = []
        with self._lock:
            self._history.clear()
            self._close_active("model", events)
        self._notify(events)

    def update(
        self,
        *,
        confirmed: Iterable[str],
        candidates: Iterable[Candidate],
        usable: bool = True,
    ) -> List[HintEvent]:
        events: List[HintEvent] = []
        with self._lock:
            now = self._clock()
            self._purge(now)

            if not usable:
                self._history.clear()
                self._close_active("model", events)
            else:
                confirmed_list = list(confirmed)
                known = {name for name in confirmed_list if name in CLASS_INFO}

                for name in known:
                    if name in self._emit_count:
                        self._emit_count[name] = 0  # reinicia repeticiones; la espera base se conserva
                if self._active is not None and self._active.class_name in known:
                    self._close_active("confirmed", events)

                best: Optional[Candidate] = None
                for cand in candidates:
                    if cand.class_name not in CLASS_INFO or cand.class_name in known:
                        continue  # clase desconocida o ya confirmada en este fotograma
                    if best is None or cand.confidence > best.confidence:
                        best = cand
                self._history.append(best)

                stats = self._window_stats()
                if self._active is not None and stats.get(self._active.class_name, (0, 0.0))[0] == 0:
                    self._close_active("lost", events)
                # Una clase confirmada ahora mismo nunca se avisa, aunque queden muestras suyas en la ventana.
                self._maybe_emit(now, stats, events, exclude=known)

        self._notify(events)
        return events

    # ------------------------------------------------------------------ interno
    def _window_stats(self) -> Dict[str, Tuple[int, float]]:
        acc: Dict[str, Tuple[int, float]] = {}
        for sample in self._history:
            if sample is None:
                continue
            count, total = acc.get(sample.class_name, (0, 0.0))
            acc[sample.class_name] = (count + 1, total + sample.confidence)
        return {name: (count, total / count) for name, (count, total) in acc.items()}

    def _muted_remaining(self, name: str, now: float) -> float:
        return max(self._muted_until.get(name, 0.0) - now, 0.0)

    def _class_wait(self, name: str) -> float:
        k = max(self._emit_count.get(name, 0), 1)
        grown = self._cfg.cooldown_seconds * (self._cfg.backoff_factor ** min(k - 1, _MAX_BACKOFF_EXPONENT))
        return min(grown, max(self._cfg.cooldown_max_seconds, self._cfg.cooldown_seconds))

    def _maybe_emit(
        self,
        now: float,
        stats: Dict[str, Tuple[int, float]],
        events: List[HintEvent],
        exclude: Iterable[str] = (),
    ) -> None:
        skip = set(exclude)
        eligible = sorted(
            (
                (count, mean, name)
                for name, (count, mean) in stats.items()
                if count >= self._cfg.min_frames and name not in skip
            ),
            reverse=True,
        )
        if not eligible:
            return
        if self._last_global is not None and now - self._last_global < self._cfg.global_cooldown_seconds:
            return
        for _count, mean, name in eligible:
            if self._muted_remaining(name, now) > 0:
                continue  # marcada como falso positivo: se prueba con la siguiente candidata
            last = self._last_emit_at.get(name)
            if last is not None and now - last < self._class_wait(name):
                continue
            self._emit(now, name, mean, events)
            return

    def _emit(self, now: float, name: str, confidence: float, events: List[HintEvent]) -> None:
        if self._active is not None:
            self._close_active("superseded", events)
        hint = HintEmitted(
            hint_id=self._new_id(),
            class_name=name,
            confidence=round(confidence, 4),
            message=build_hint_message(name),
            ts=int(self._wall()),
        )
        self._active = hint
        self._last_emit_at[name] = now
        self._emit_count[name] = self._emit_count.get(name, 0) + 1
        self._last_global = now
        self._emitted_total += 1
        self._registry[hint.hint_id] = (hint, now)
        while len(self._registry) > MAX_REGISTRY_ENTRIES:
            self._registry.popitem(last=False)
        events.append(hint)

    def _close_active(self, reason: str, events: List[HintEvent]) -> None:
        if self._active is None:
            return
        events.append(HintCleared(self._active.hint_id, self._active.class_name, reason))
        self._active = None

    def _purge(self, now: float) -> None:
        for name in [n for n, until in self._muted_until.items() if until <= now]:
            del self._muted_until[name]
        ttl = self._cfg.registry_ttl_seconds
        while self._registry:
            hint_id, (_hint, created) = next(iter(self._registry.items()))
            if now - created <= ttl:
                break
            self._registry.pop(hint_id)

    def _notify(self, events: List[HintEvent]) -> None:
        if not events:
            return
        with self._lock:
            listeners = list(self._listeners)
        for event in events:
            for listener in listeners:
                try:
                    listener(event)
                except Exception:
                    logger.exception("Un suscriptor de avisos fallo (se ignora).")
