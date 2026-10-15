"""
Pruebas del WebSocket de avisos (Fase 2): hub, tickets, autenticacion, origen, limites y
extremo a extremo con /detect y un modelo falso. Sin camara, pesos ni red.

    python -m unittest discover -s tests -v
"""

import asyncio
import os
import sys
import threading
import unittest
from contextlib import asynccontextmanager
from unittest import mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("SERIAL_MOCK", "true")

from hints import Candidate, HintCleared, HintConfig, HintConfigError, HintEmitted, HintEngine  # noqa: E402
from hints.hub import HintHub, TicketStore, event_to_message  # noqa: E402

try:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from starlette.websockets import WebSocketDisconnect

    from hints.api import router, shutdown_hints, startup_hints

    API_AVAILABLE = True
except Exception:  # pragma: no cover
    API_AVAILABLE = False

try:
    import server  # noqa: E402
    from test_server_hints import FakeModel, jpeg_bytes  # noqa: E402

    SERVER_AVAILABLE = True
except Exception:  # pragma: no cover
    SERVER_AVAILABLE = False


class FakeClock:
    def __init__(self, start: float = 100.0):
        self.now = start

    def __call__(self) -> float:
        return self.now


def emitted_event(hint_id="h_test_0001", name="cautin"):
    return HintEmitted(hint_id, name, 0.45, f"Creo haber visto {name}.", 1_760_000_000)


# ---------------------------------------------------------------------- configuracion
class WsConfigTests(unittest.TestCase):
    def test_defaults(self):
        cfg = HintConfig.from_env({})
        self.assertEqual((cfg.ws_max_clients, cfg.ws_ticket_ttl_seconds), (8, 30.0))
        self.assertEqual((cfg.ws_allowed_origins, cfg.backend_key), ((), ""))

    def test_origins_are_normalized(self):
        cfg = HintConfig.from_env({"WS_ALLOWED_ORIGINS": "http://localhost:3000/, HTTPS://App.Vercel.app ,"})
        self.assertEqual(cfg.ws_allowed_origins, ("http://localhost:3000", "https://app.vercel.app"))

    def test_invalid_origin_and_limits(self):
        for env in (
            {"WS_ALLOWED_ORIGINS": "*"},
            {"WS_ALLOWED_ORIGINS": "localhost:3000"},
            {"WS_ALLOWED_ORIGINS": "http://a.com/ruta"},
            {"WS_MAX_CLIENTS": "0"},
            {"WS_TICKET_TTL_SECONDS": "1"},
        ):
            with self.subTest(env=env), self.assertRaises(HintConfigError):
                HintConfig.from_env(env)

    def test_backend_key_falls_back_to_voice_key(self):
        self.assertEqual(HintConfig.from_env({"VOICE_BACKEND_KEY": "k1"}).backend_key, "k1")
        self.assertEqual(HintConfig.from_env({"VOICE_BACKEND_KEY": "k1", "HINT_BACKEND_KEY": "k2"}).backend_key, "k2")

    def test_key_never_in_repr_or_summary(self):
        cfg = HintConfig.from_env({"HINT_BACKEND_KEY": "super-secreta"})
        self.assertNotIn("super-secreta", repr(cfg))
        self.assertNotIn("super-secreta", cfg.summary())


# ---------------------------------------------------------------------- contrato y tickets
class ContractTests(unittest.TestCase):
    def test_message_shapes_match_contract_v1(self):
        self.assertEqual(
            event_to_message(emitted_event()),
            {
                "v": 1,
                "type": "low_confidence",
                "hint_id": "h_test_0001",
                "class": "cautin",
                "confidence": 0.45,
                "message": "Creo haber visto cautin.",
                "ts": 1_760_000_000,
            },
        )
        self.assertEqual(
            event_to_message(HintCleared("h_test_0001", "cautin", "lost")),
            {"v": 1, "type": "hint_cleared", "hint_id": "h_test_0001"},
        )
        self.assertIsNone(event_to_message(object()))


class TicketStoreTests(unittest.TestCase):
    def test_single_use(self):
        store = TicketStore(30, clock=FakeClock())
        ticket = store.issue()
        self.assertTrue(store.consume(ticket))
        self.assertFalse(store.consume(ticket))

    def test_expiry_and_garbage(self):
        clock = FakeClock()
        store = TicketStore(30, clock=clock)
        ticket = store.issue()
        clock.now += 31
        self.assertFalse(store.consume(ticket))
        for bad in (None, "", "no-existe"):
            self.assertFalse(store.consume(bad))

    def test_tickets_are_unique_and_bounded(self):
        store = TicketStore(30, clock=FakeClock())
        tokens = [store.issue() for _ in range(300)]
        self.assertEqual(len(set(tokens)), 300)
        self.assertLessEqual(len(store._tickets), 256)
        self.assertFalse(store.consume(tokens[0]))  # los mas antiguos se descartan
        self.assertTrue(store.consume(tokens[-1]))


# ---------------------------------------------------------------------- hub
class HubTests(unittest.IsolatedAsyncioTestCase):
    async def test_slow_client_queue_is_bounded_and_keeps_latest(self):
        hub = HintHub(HintConfig(), asyncio.get_running_loop())
        client = hub.add_client()
        for i in range(20):
            hub._broadcast({"i": i})
        self.assertEqual(client.queue.qsize(), 16)
        items = [client.queue.get_nowait()["i"] for _ in range(16)]
        self.assertEqual(items, list(range(4, 20)))
        self.assertEqual(hub.stats()["dropped"], 4)

    async def test_client_limit(self):
        hub = HintHub(HintConfig(ws_max_clients=2), asyncio.get_running_loop())
        a, b = hub.add_client(), hub.add_client()
        self.assertIsNotNone(a)
        self.assertIsNone(hub.add_client())
        hub.remove_client(a)
        self.assertIsNotNone(hub.add_client())
        self.assertEqual(hub.client_count, 2)
        self.assertIsNotNone(b)

    async def test_publish_from_another_thread_reaches_clients(self):
        loop = asyncio.get_running_loop()
        hub = HintHub(HintConfig(), loop)
        client = hub.add_client()
        thread = threading.Thread(target=hub.publish_threadsafe, args=(emitted_event(),))
        thread.start()
        message = await asyncio.wait_for(client.queue.get(), timeout=2)
        thread.join()
        self.assertEqual(message["type"], "low_confidence")

    async def test_close_all_sends_sentinel(self):
        hub = HintHub(HintConfig(), asyncio.get_running_loop())
        client = hub.add_client()
        for i in range(16):  # cola llena: el centinela debe entrar igualmente
            client.queue.put_nowait({"i": i})
        hub.close_all()
        last = None
        while not client.queue.empty():
            last = client.queue.get_nowait()
        self.assertIsNone(last)

    def test_publish_after_loop_closed_does_not_raise(self):
        loop = asyncio.new_event_loop()
        hub = HintHub(HintConfig(), loop)
        loop.close()
        hub.publish_threadsafe(emitted_event())  # no debe lanzar


# ---------------------------------------------------------------------- API (WebSocket + ticket)
def make_app(config, *, enabled=True):
    holder = {}

    @asynccontextmanager
    async def lifespan(app):
        app.state.hint_engine = HintEngine(config) if enabled else None
        holder["engine"] = app.state.hint_engine
        await startup_hints(app)
        yield
        await shutdown_hints(app)

    app = FastAPI(lifespan=lifespan)
    app.include_router(router)
    return app, holder


def drive_hint(engine, name="cautin", conf=0.45, frames=5):
    for _ in range(frames):
        engine.update(confirmed=[], candidates=[Candidate(name, conf)])


@unittest.skipUnless(API_AVAILABLE, "fastapi no disponible")
class OpenModeApiTests(unittest.TestCase):
    def test_hello_then_hint_then_cleared(self):
        app, holder = make_app(HintConfig(global_cooldown_seconds=0))
        with TestClient(app) as client, client.websocket_connect("/ws/hints") as ws:
            self.assertEqual(ws.receive_json(), {"v": 1, "type": "hello"})
            drive_hint(holder["engine"])
            message = ws.receive_json()
            self.assertEqual(message["type"], "low_confidence")
            self.assertEqual(message["class"], "cautin")
            self.assertEqual(set(message), {"v", "type", "hint_id", "class", "confidence", "message", "ts"})
            self.assertIn("Creo haber visto un cautín", message["message"])
            holder["engine"].update(confirmed=["cautin"], candidates=[])
            self.assertEqual(ws.receive_json(), {"v": 1, "type": "hint_cleared", "hint_id": message["hint_id"]})

    def test_hint_reaches_every_client(self):
        app, holder = make_app(HintConfig())
        with TestClient(app) as client:
            with client.websocket_connect("/ws/hints") as a, client.websocket_connect("/ws/hints") as b:
                a.receive_json(), b.receive_json()
                drive_hint(holder["engine"])
                self.assertEqual(a.receive_json()["hint_id"], b.receive_json()["hint_id"])

    def test_client_limit_closes_with_1013(self):
        app, _ = make_app(HintConfig(ws_max_clients=1))
        with TestClient(app) as client, client.websocket_connect("/ws/hints") as first:
            first.receive_json()
            with client.websocket_connect("/ws/hints") as second, self.assertRaises(WebSocketDisconnect) as ctx:
                second.receive_json()
            self.assertEqual(ctx.exception.code, 1013)

    def test_oversized_client_message_closes_with_1009(self):
        app, _ = make_app(HintConfig())
        with TestClient(app) as client, client.websocket_connect("/ws/hints") as ws:
            ws.receive_json()
            ws.send_text("x" * 5000)
            with self.assertRaises(WebSocketDisconnect) as ctx:
                ws.receive_json()
            self.assertEqual(ctx.exception.code, 1009)

    def test_slot_is_released_when_client_disconnects(self):
        app, _ = make_app(HintConfig(ws_max_clients=1))
        with TestClient(app) as client:
            with client.websocket_connect("/ws/hints") as ws:
                ws.receive_json()
            with client.websocket_connect("/ws/hints") as again:
                self.assertEqual(again.receive_json(), {"v": 1, "type": "hello"})

    def test_ticket_endpoint_issues_even_in_open_mode(self):
        app, _ = make_app(HintConfig())
        with TestClient(app) as client:
            response = client.post("/hints/ticket")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["expires_in"], 30)
            self.assertEqual(response.headers["cache-control"], "no-store")


@unittest.skipUnless(API_AVAILABLE, "fastapi no disponible")
class AuthApiTests(unittest.TestCase):
    CFG = dict(backend_key="clave-secreta")

    def _expect_close(self, client, url, code, **kwargs):
        with client.websocket_connect(url, **kwargs) as ws, self.assertRaises(WebSocketDisconnect) as ctx:
            ws.receive_json()
        self.assertEqual(ctx.exception.code, code)

    def test_ticket_endpoint_requires_key(self):
        app, _ = make_app(HintConfig(**self.CFG))
        with TestClient(app) as client:
            missing = client.post("/hints/ticket")
            self.assertEqual((missing.status_code, missing.json()["error"]["code"]), (401, "unauthorized"))
            wrong = client.post("/hints/ticket", headers={"X-Backend-Key": "otra"})
            self.assertEqual((wrong.status_code, wrong.json()["error"]["code"]), (403, "forbidden"))
            ok = client.post("/hints/ticket", headers={"X-Backend-Key": "clave-secreta"})
            self.assertEqual(ok.status_code, 200)
            self.assertNotIn("clave-secreta", ok.text)

    def test_websocket_requires_valid_single_use_ticket(self):
        app, _ = make_app(HintConfig(**self.CFG))
        with TestClient(app) as client:
            self._expect_close(client, "/ws/hints", 4401)
            self._expect_close(client, "/ws/hints?ticket=inventado", 4401)
            ticket = client.post("/hints/ticket", headers={"X-Backend-Key": "clave-secreta"}).json()["ticket"]
            with client.websocket_connect(f"/ws/hints?ticket={ticket}") as ws:
                self.assertEqual(ws.receive_json(), {"v": 1, "type": "hello"})
            self._expect_close(client, f"/ws/hints?ticket={ticket}", 4401)  # ya gastado

    def test_failed_capacity_check_does_not_leak_other_clients(self):
        app, _ = make_app(HintConfig(ws_max_clients=1, **self.CFG))
        with TestClient(app) as client:
            issue = lambda: client.post("/hints/ticket", headers={"X-Backend-Key": "clave-secreta"}).json()["ticket"]
            with client.websocket_connect(f"/ws/hints?ticket={issue()}") as first:
                first.receive_json()
                self._expect_close(client, f"/ws/hints?ticket={issue()}", 1013)


@unittest.skipUnless(API_AVAILABLE, "fastapi no disponible")
class OriginAndDisabledTests(unittest.TestCase):
    def test_origin_allowlist(self):
        app, _ = make_app(HintConfig(ws_allowed_origins=("http://localhost:3000",)))
        with TestClient(app) as client:
            with self.assertRaises(WebSocketDisconnect) as ctx:
                with client.websocket_connect("/ws/hints", headers={"origin": "https://malo.example"}) as ws:
                    ws.receive_json()
            self.assertEqual(ctx.exception.code, 4403)
            with client.websocket_connect("/ws/hints", headers={"origin": "http://localhost:3000/"}) as ws:
                self.assertEqual(ws.receive_json()["type"], "hello")
            with client.websocket_connect("/ws/hints") as ws:  # sin Origin: cliente no navegador
                self.assertEqual(ws.receive_json()["type"], "hello")

    def test_disabled_hints(self):
        app, _ = make_app(HintConfig(), enabled=False)
        with TestClient(app) as client:
            response = client.post("/hints/ticket")
            self.assertEqual((response.status_code, response.json()["error"]["code"]), (503, "hints_disabled"))
            with self.assertRaises(WebSocketDisconnect) as ctx:
                with client.websocket_connect("/ws/hints") as ws:
                    ws.receive_json()
            self.assertEqual(ctx.exception.code, 4503)


# ---------------------------------------------------------------------- extremo a extremo con server.py
@unittest.skipUnless(API_AVAILABLE and SERVER_AVAILABLE, "dependencias del servidor no disponibles")
class ServerEndToEndTests(unittest.TestCase):
    def setUp(self):
        @asynccontextmanager
        async def fake_detector(app):
            app.state.model = FakeModel([[(3, 0.45)]])  # 3 = cautin, entre LOW_CONF y CONF
            app.state.is_fallback = False
            yield

        patcher = mock.patch.object(server, "_detector_lifespan", fake_detector)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _detect(self, client):
        return client.post("/detect", files={"image": ("f.jpg", jpeg_bytes(), "image/jpeg")})

    def test_detect_publishes_hint_over_websocket_without_changing_response(self):
        with mock.patch.dict(os.environ, {"HINT_GLOBAL_COOLDOWN_SECONDS": "0"}):
            with TestClient(server.app) as client, client.websocket_connect("/ws/hints") as ws:
                self.assertEqual(ws.receive_json(), {"v": 1, "type": "hello"})
                for _ in range(5):
                    response = self._detect(client)
                    self.assertEqual(
                        response.json(),
                        {"class": None, "confidence": None, "detections": 0, "boxes": [], "model": "custom"},
                    )
                message = ws.receive_json()
                self.assertEqual((message["type"], message["class"]), ("low_confidence", "cautin"))
                self.assertIn("Creo haber visto un cautín", message["message"])

    def test_full_flow_with_backend_key_and_ticket(self):
        env = {"HINT_BACKEND_KEY": "clave-e2e", "WS_ALLOWED_ORIGINS": "http://localhost:3000"}
        with mock.patch.dict(os.environ, env):
            with TestClient(server.app) as client:
                self.assertEqual(client.post("/hints/ticket").status_code, 401)
                ticket = client.post("/hints/ticket", headers={"X-Backend-Key": "clave-e2e"}).json()["ticket"]
                headers = {"origin": "http://localhost:3000"}
                with client.websocket_connect(f"/ws/hints?ticket={ticket}", headers=headers) as ws:
                    ws.receive_json()
                    for _ in range(5):
                        self._detect(client)
                    self.assertEqual(ws.receive_json()["type"], "low_confidence")

    def test_hints_disabled_leaves_detect_and_websocket_off(self):
        with mock.patch.dict(os.environ, {"HINTS_ENABLED": "false"}):
            with TestClient(server.app) as client:
                self.assertEqual(self._detect(client).status_code, 200)
                self.assertEqual(client.post("/hints/ticket").status_code, 503)


if __name__ == "__main__":
    unittest.main()
