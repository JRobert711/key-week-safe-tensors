"""
HintHub: reparte los avisos del HintEngine a los clientes WebSocket (sin dependencias de FastAPI).

  - publish_threadsafe(event) se llama desde el HILO DE INFERENCIA: serializa y delega en el event loop
    con call_soon_threadsafe; nunca espera ni bloquea la deteccion (si el loop ya cerro, se ignora).
  - Cada cliente tiene una cola acotada; si un cliente lento la llena se descarta lo mas antiguo.
  - Maximo de clientes simultaneos (ws_max_clients).
  - TicketStore: tickets aleatorios de un solo uso con TTL corto, solo en memoria del proceso.

Contrato v1 (servidor -> cliente):
  {"v":1,"type":"hello"}
  {"v":1,"type":"low_confidence","hint_id","class","confidence","message","ts"}
  {"v":1,"type":"hint_cleared","hint_id"}
"""

from __future__ import annotations

import asyncio
import logging
import secrets
import time
from collections import OrderedDict
from typing import Any, Callable, Dict, List, Optional

from .config import HintConfig
from .engine import HintCleared, HintEmitted

logger = logging.getLogger("HintHub")

PROTOCOL_VERSION = 1
CLIENT_QUEUE_SIZE = 16
MAX_OUTSTANDING_TICKETS = 256


def event_to_message(event: Any) -> Optional[Dict[str, Any]]:
    """Convierte un evento del motor en el mensaje del contrato v1 (None si no es un evento conocido)."""
    if isinstance(event, HintEmitted):
        return {
            "v": PROTOCOL_VERSION,
            "type": "low_confidence",
            "hint_id": event.hint_id,
            "class": event.class_name,
            "confidence": event.confidence,
            "message": event.message,
            "ts": event.ts,
        }
    if isinstance(event, HintCleared):
        return {"v": PROTOCOL_VERSION, "type": "hint_cleared", "hint_id": event.hint_id}
    return None


class TicketStore:
    """Tickets de un solo uso. 256 bits de aleatoriedad: no hace falta firmarlos, solo recordarlos."""

    def __init__(self, ttl_seconds: float, *, clock: Callable[[], float] = time.monotonic):
        self._ttl = ttl_seconds
        self._clock = clock
        self._tickets: "OrderedDict[str, float]" = OrderedDict()

    @property
    def ttl_seconds(self) -> float:
        return self._ttl

    def issue(self) -> str:
        now = self._clock()
        self._purge(now)
        token = secrets.token_urlsafe(32)
        self._tickets[token] = now + self._ttl
        while len(self._tickets) > MAX_OUTSTANDING_TICKETS:
            self._tickets.popitem(last=False)
        return token

    def consume(self, token: Optional[str]) -> bool:
        """True si el ticket existe y no expiro; en cualquier caso queda gastado."""
        if not token:
            return False
        expires = self._tickets.pop(token, None)
        return expires is not None and self._clock() <= expires

    def _purge(self, now: float) -> None:
        while self._tickets:
            token, expires = next(iter(self._tickets.items()))
            if expires >= now:
                break
            self._tickets.pop(token)


class HintClient:
    def __init__(self, queue_size: int = CLIENT_QUEUE_SIZE):
        self.queue: "asyncio.Queue[Optional[Dict[str, Any]]]" = asyncio.Queue(maxsize=queue_size)


class HintHub:
    def __init__(
        self,
        config: HintConfig,
        loop: asyncio.AbstractEventLoop,
        *,
        clock: Callable[[], float] = time.monotonic,
    ):
        self.config = config
        self.tickets = TicketStore(config.ws_ticket_ttl_seconds, clock=clock)
        self._loop = loop
        self._clients: List[HintClient] = []
        self._published = 0
        self._dropped = 0

    # ------------------------------------------------------------------ clientes (hilo del loop)
    @property
    def client_count(self) -> int:
        return len(self._clients)

    def add_client(self) -> Optional[HintClient]:
        if len(self._clients) >= self.config.ws_max_clients:
            return None
        client = HintClient()
        self._clients.append(client)
        return client

    def remove_client(self, client: HintClient) -> None:
        try:
            self._clients.remove(client)
        except ValueError:
            pass

    def stats(self) -> Dict[str, int]:
        return {"clients": len(self._clients), "published": self._published, "dropped": self._dropped}

    def close_all(self) -> None:
        """Pide a cada cliente que cierre (centinela None en su cola); se usa al apagar el servidor."""
        for client in list(self._clients):
            while True:
                try:
                    client.queue.put_nowait(None)  # type: ignore[arg-type]
                    break
                except asyncio.QueueFull:
                    try:
                        client.queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break

    # ------------------------------------------------------------------ publicacion
    def publish_threadsafe(self, event: Any) -> None:
        """Seguro desde cualquier hilo. No bloquea ni lanza: un fallo aqui nunca toca la deteccion."""
        try:
            message = event_to_message(event)
            if message is None:
                return
            self._loop.call_soon_threadsafe(self._broadcast, message)
        except RuntimeError:
            pass  # el loop ya se cerro (apagado del servidor)
        except Exception:
            logger.exception("No se pudo publicar el aviso al WebSocket.")

    def _broadcast(self, message: Dict[str, Any]) -> None:
        self._published += 1
        for client in list(self._clients):
            try:
                client.queue.put_nowait(message)
            except asyncio.QueueFull:
                # Cliente lento: se descarta lo mas antiguo para que el aviso nuevo no se pierda.
                try:
                    client.queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    client.queue.put_nowait(message)
                except asyncio.QueueFull:
                    pass
                self._dropped += 1
