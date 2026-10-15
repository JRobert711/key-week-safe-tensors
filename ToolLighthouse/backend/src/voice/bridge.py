"""
VoiceBridge: cola acotada + workers + idempotencia, aislado del detector.

Orden de decisiones en synthesize_detection():
  1. Dedupe  (event_id, o sesion+transicion+clase)  -> reutiliza el resultado, sin coste.
  2. Orden   (transition_id menor que el ultimo de la sesion)  -> 409 stale_event.
  3. Intervalo minimo por sesion (VOICE_MIN_INTERVAL_SECONDS)  -> 429 rate_limited + Retry-After.
  4. Cache por clase (modelo+voz+texto)                         -> respuesta inmediata.
  5. Cola acotada -> worker -> proveedor (timeout, 1 reintento solo en fallos transitorios).

Politica de cola llena: se descarta el anuncio rutinario PENDIENTE mas antiguo (obsoleto) y se
admite el nuevo; si no hay nada descartable, 429 queue_full. Nunca crece sin limite.
Al admitir una transicion nueva, los anuncios pendientes de la MISMA sesion con transicion
anterior se descartan (409 stale_event) antes de gastar una llamada al proveedor.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import math
import time
from collections import OrderedDict
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional, Tuple

from .audio_cache import AudioCache, make_key
from .config import VoiceConfig
from .message_builder import build_detection_message
from .provider import (
    AudioResult,
    InvalidEventError,
    ProviderError,
    ProviderTimeoutError,
    ProviderTransientError,
    QueueFullError,
    RateLimitedError,
    StaleEventError,
    TTSProvider,
    VoiceDisabledError,
    VoiceError,
)

logger = logging.getLogger("VoiceBridge")

MAX_SPEAK_CHARS = 200
SESSION_TTL_SECONDS = 3600.0
MAX_SESSIONS = 256
RETRY_BACKOFF_SECONDS = 0.25


@dataclass
class _Session:
    last_transition: int = -1
    last_accept: Optional[float] = None
    last_seen: float = 0.0


@dataclass
class _Job:
    text: str
    class_name: Optional[str]
    priority: int
    seq: int
    future: "asyncio.Future[AudioResult]"
    session_id: Optional[str] = None
    transition_id: Optional[int] = None

    @property
    def routine(self) -> bool:
        return self.priority <= 0


def _retrieve_exception(fut: "asyncio.Future") -> None:
    # Marca la excepcion como leida: evita "Future exception was never retrieved".
    if not fut.cancelled():
        fut.exception()


class VoiceBridge:
    def __init__(
        self,
        config: VoiceConfig,
        provider: Optional[TTSProvider],
        *,
        clock: Callable[[], float] = time.monotonic,
        disabled_reason: Optional[str] = None,
    ):
        self._cfg = config
        self._provider = provider
        self._clock = clock
        self._disabled_reason = disabled_reason
        self._runtime_enabled = True
        self._closed = False
        self._cache = AudioCache(config.cache_max_entries)
        self._pending: List[_Job] = []
        self._wakeup = asyncio.Event()
        self._workers: List["asyncio.Task[None]"] = []
        self._seq = 0
        self._sessions: "OrderedDict[str, _Session]" = OrderedDict()
        self._dedupe: "OrderedDict[str, Tuple[float, asyncio.Future]]" = OrderedDict()
        self._stats: Dict[str, Any] = {
            "requests": 0,
            "low_confidence_requests": 0,
            "ok": 0,
            "cache_hits": 0,
            "deduplicated": 0,
            "stale": 0,
            "throttled": 0,
            "queue_full": 0,
            "dropped_obsolete": 0,
            "synth_count": 0,
            "synth_total_ms": 0.0,
            "synth_last_ms": None,
            "queue_peak": 0,
            "errors": {},
        }

    # ------------------------------------------------------------------ ciclo de vida
    def start(self) -> None:
        """Crea los workers. Requiere un event loop en marcha (se llama desde el lifespan)."""
        if self._provider is None or self._workers:
            return
        for i in range(self._cfg.max_concurrent_synthesis):
            self._workers.append(asyncio.create_task(self._worker(i), name=f"voice-worker-{i}"))

    async def shutdown(self) -> None:
        self._closed = True
        for task in self._workers:
            task.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
        for job in self._pending:
            if not job.future.done():
                job.future.set_exception(VoiceDisabledError())
        self._pending.clear()
        self._cache.clear()
        if self._provider is not None:
            try:
                await self._provider.close()
            except Exception as exc:  # cerrar nunca debe romper el apagado del servidor
                logger.warning("Error al cerrar el proveedor de voz: %s", type(exc).__name__)

    # ------------------------------------------------------------------ estado
    @property
    def config(self) -> VoiceConfig:
        return self._cfg

    @property
    def available(self) -> bool:
        return (
            self._cfg.enabled
            and self._runtime_enabled
            and self._provider is not None
            and not self._closed
        )

    def set_enabled(self, enabled: bool) -> None:
        """Interruptor en caliente (no reinicia nada). Sin proveedor no puede activarse."""
        self._runtime_enabled = bool(enabled)

    def status(self) -> Dict[str, Any]:
        stats = self._stats
        count = stats["synth_count"]
        return {
            "available": self.available,
            "reason": None if self.available else (self._disabled_reason or "disabled"),
            "model": self._cfg.model,
            "queue_size": len(self._pending),
            "queue_max": self._cfg.max_queue_size,
            "cache_entries": len(self._cache),
            "sessions": len(self._sessions),
            "counters": {k: v for k, v in stats.items() if k not in ("synth_total_ms", "synth_last_ms")},
            "synth_last_ms": stats["synth_last_ms"],
            "synth_avg_ms": round(stats["synth_total_ms"] / count, 1) if count else None,
        }

    # ------------------------------------------------------------------ API publica
    async def synthesize_detection(
        self,
        class_names: List[str],
        *,
        event_id: str,
        session_id: str,
        transition_id: int,
    ) -> AudioResult:
        self._stats["requests"] += 1
        self._ensure_available()
        from .message_builder import normalize_class_names

        normalized = normalize_class_names(class_names)
        text = build_detection_message(normalized, self._cfg.language)
        class_names_key = ",".join(normalized)
        now = self._clock()
        self._purge(now)

        # 1) Dedupe por evento o por sesión/transición/conjunto.
        keys = (f"e:{event_id}", f"t:{session_id}:{transition_id}:{class_names_key}")
        existing = self._dedupe_get(keys, now)
        if existing is not None:
            self._stats["deduplicated"] += 1
            return await asyncio.shield(existing)

        # 2) Orden de transiciones.
        session = self._session(session_id, now)
        if transition_id < session.last_transition:
            self._stats["stale"] += 1
            raise StaleEventError()

        # 3) Intervalo mínimo por sesión.
        interval = self._cfg.min_interval_seconds
        if session.last_accept is not None and now - session.last_accept < interval:
            self._stats["throttled"] += 1
            raise RateLimitedError(retry_after=max(1, math.ceil(interval - (now - session.last_accept))))

        # 4) Caché / 5) cola. La clave de caché real se basa en el texto completo.
        fut = self._admit(text, class_names_key, priority=0, session_id=session_id, transition_id=transition_id)
        session.last_transition = transition_id
        session.last_accept = now
        self._dedupe_put(keys, fut, now)
        return await asyncio.shield(fut)

    async def synthesize_hint(
        self,
        resolve: Callable[[], Tuple[str, str]],
        *,
        event_id: str,
        hint_id: str,
    ) -> AudioResult:
        """Voz de un aviso de baja confianza ("Creo haber visto ...").

        `resolve()` devuelve (texto, clase) a partir del aviso registrado en el HintEngine (tabla
        cerrada: el texto nunca viene del cliente) y puede lanzar VoiceError si el aviso no existe o
        ya no esta vigente. Solo se invoca si la peticion NO es un duplicado, de modo que un reintento
        del mismo event_id / hint_id reutiliza el audio aunque el aviso ya se haya cerrado.

        Es rutinario (prioridad 0: se descarta antes que cualquier otro si la cola se llena) y NO usa
        sesion ni transition_id, asi que no interfiere con el orden ni el intervalo de los anuncios de
        deteccion. Las esperas anti-repeticion ya las aplica el HintEngine: no hay intervalo propio.
        """
        self._stats["requests"] += 1
        self._stats["low_confidence_requests"] += 1
        self._ensure_available()
        now = self._clock()
        self._purge(now)

        keys = (f"e:{event_id}", f"h:{hint_id}")
        existing = self._dedupe_get(keys, now)
        if existing is not None:
            self._stats["deduplicated"] += 1
            return await asyncio.shield(existing)

        text, class_name = resolve()
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_SPEAK_CHARS:
            raise InvalidEventError(f"El texto debe tener entre 1 y {MAX_SPEAK_CHARS} caracteres.")
        fut = self._admit(text.strip(), class_name, priority=0)
        self._dedupe_put(keys, fut, now)
        return await asyncio.shield(fut)

    async def speak(self, text: str, *, priority: int = 0) -> AudioResult:
        """Interfaz INTERNA de confianza (no expuesta por HTTP). Prioridad efectiva >= 1."""
        self._stats["requests"] += 1
        self._ensure_available()
        if not isinstance(text, str) or not text.strip() or len(text) > MAX_SPEAK_CHARS:
            raise InvalidEventError(f"El texto debe tener entre 1 y {MAX_SPEAK_CHARS} caracteres.")
        fut = self._admit(text.strip(), None, priority=max(1, priority))
        return await asyncio.shield(fut)

    # ------------------------------------------------------------------ admision y cola
    def _ensure_available(self) -> None:
        if not self.available:
            raise VoiceDisabledError()

    def _admit(
        self,
        text: str,
        class_name: Optional[str],
        *,
        priority: int,
        session_id: Optional[str] = None,
        transition_id: Optional[int] = None,
    ) -> "asyncio.Future[AudioResult]":
        loop = asyncio.get_running_loop()
        fut: "asyncio.Future[AudioResult]" = loop.create_future()
        fut.add_done_callback(_retrieve_exception)

        cached = self._cache.get(make_key(self._cfg.model, self._cfg.reference_id, text))
        if cached is not None:
            self._stats["cache_hits"] += 1
            self._stats["ok"] += 1
            fut.set_result(dataclasses.replace(cached, class_name=class_name, cached=True))
            return fut

        self._seq += 1
        job = _Job(text, class_name, priority, self._seq, fut, session_id, transition_id)
        self._enqueue(job)
        return fut

    def _enqueue(self, job: _Job) -> None:
        # Anuncios pendientes de la misma sesion con transicion anterior: obsoletos.
        if job.session_id is not None and job.transition_id is not None:
            for old in [
                j
                for j in self._pending
                if j.session_id == job.session_id
                and j.transition_id is not None
                and j.transition_id < job.transition_id
            ]:
                self._discard(old)

        if len(self._pending) >= self._cfg.max_queue_size:
            victim = next((j for j in sorted(self._pending, key=lambda j: j.seq) if j.routine), None)
            if victim is None:
                self._stats["queue_full"] += 1
                raise QueueFullError()
            self._discard(victim)

        self._pending.append(job)
        self._pending.sort(key=lambda j: (-j.priority, j.seq))
        self._stats["queue_peak"] = max(self._stats["queue_peak"], len(self._pending))
        self._wakeup.set()

    def _discard(self, job: _Job) -> None:
        if job in self._pending:
            self._pending.remove(job)
        self._stats["dropped_obsolete"] += 1
        if not job.future.done():
            job.future.set_exception(StaleEventError())

    # ------------------------------------------------------------------ workers
    async def _next_job(self) -> _Job:
        while True:
            if self._pending:
                return self._pending.pop(0)
            self._wakeup.clear()
            await self._wakeup.wait()

    async def _worker(self, index: int) -> None:
        while True:
            job = await self._next_job()
            try:
                await self._process(job)
            except asyncio.CancelledError:
                if not job.future.done():
                    job.future.set_exception(VoiceDisabledError())
                raise
            except Exception:
                logger.exception("Error inesperado en el worker de voz")
                if not job.future.done():
                    job.future.set_exception(ProviderError())

    async def _process(self, job: _Job) -> None:
        fut = job.future
        if fut.done():
            return
        key = make_key(self._cfg.model, self._cfg.reference_id, job.text)
        cached = self._cache.get(key)  # pudo llenarse mientras esperaba en cola
        if cached is not None:
            self._stats["cache_hits"] += 1
            self._stats["ok"] += 1
            fut.set_result(dataclasses.replace(cached, class_name=job.class_name, cached=True))
            return
        if not self.available:
            fut.set_exception(VoiceDisabledError())
            return

        started = time.perf_counter()
        try:
            produced = await self._call_provider(job.text)
        except VoiceError as exc:
            errors = self._stats["errors"]
            errors[exc.code] = errors.get(exc.code, 0) + 1
            fut.set_exception(exc)
            return

        elapsed_ms = (time.perf_counter() - started) * 1000
        result = AudioResult(
            audio=produced.audio,
            mime=produced.mime,
            model=self._cfg.model,
            class_name=job.class_name,
            cached=False,
            synth_ms=round(elapsed_ms, 1),
        )
        self._cache.put(key, result)
        stats = self._stats
        stats["synth_count"] += 1
        stats["synth_total_ms"] += elapsed_ms
        stats["synth_last_ms"] = round(elapsed_ms, 1)
        stats["ok"] += 1
        logger.info(
            "Voz sintetizada: clase=%s %.0f ms %d bytes (cola=%d)",
            job.class_name or "-",
            elapsed_ms,
            len(produced.audio),
            len(self._pending),
        )
        fut.set_result(result)

    async def _call_provider(self, text: str):
        attempts = 1 + self._cfg.max_retries
        # Tope total por intento: el proveedor ya tiene su timeout; esto es la red de seguridad.
        hard_timeout = self._cfg.request_timeout_seconds + 2.0
        for attempt in range(1, attempts + 1):
            try:
                return await asyncio.wait_for(self._provider.synthesize(text), timeout=hard_timeout)
            except asyncio.TimeoutError:
                raise ProviderTimeoutError() from None  # sin reintento: se agotaria el presupuesto de latencia
            except ProviderTransientError:
                if attempt >= attempts:
                    raise
                logger.info("Fallo transitorio del proveedor; reintento %d/%d", attempt, attempts - 1)
                await asyncio.sleep(RETRY_BACKOFF_SECONDS)
        raise ProviderError()  # inalcanzable; satisface al verificador de tipos

    # ------------------------------------------------------------------ sesiones y dedupe
    def _session(self, session_id: str, now: float) -> _Session:
        session = self._sessions.get(session_id)
        if session is None:
            session = _Session()
            self._sessions[session_id] = session
        session.last_seen = now
        self._sessions.move_to_end(session_id)
        while len(self._sessions) > MAX_SESSIONS:
            self._sessions.popitem(last=False)
        return session

    def _purge(self, now: float) -> None:
        for key, (expires, _) in list(self._dedupe.items()):
            if expires <= now:
                del self._dedupe[key]
        for sid, session in list(self._sessions.items()):
            if now - session.last_seen > SESSION_TTL_SECONDS:
                del self._sessions[sid]

    def _dedupe_get(self, keys: Tuple[str, ...], now: float) -> Optional["asyncio.Future[AudioResult]"]:
        for key in keys:
            entry = self._dedupe.get(key)
            if entry is not None and entry[0] > now:
                return entry[1]
        return None

    def _dedupe_put(self, keys: Tuple[str, ...], fut: "asyncio.Future[AudioResult]", now: float) -> None:
        expires = now + self._cfg.dedupe_ttl_seconds
        for key in keys:
            self._dedupe[key] = (expires, fut)
            self._dedupe.move_to_end(key)
        while len(self._dedupe) > self._cfg.dedupe_max_entries:
            self._dedupe.popitem(last=False)

        def _forget_failed(f: "asyncio.Future[AudioResult]") -> None:
            # Un fallo no se memoriza: el reintento del cliente debe poder volver a intentarlo.
            if f.cancelled() or f.exception() is not None:
                for key in keys:
                    entry = self._dedupe.get(key)
                    if entry is not None and entry[1] is f:
                        del self._dedupe[key]

        fut.add_done_callback(_forget_failed)
