import test from "node:test";
import assert from "node:assert/strict";
import { parseHintMessage } from "../src/lib/hintsContract";
import { HintsError } from "../src/lib/hintsApi";
import {
  CONNECT_TIMEOUT_MS, HintsConnection, RECONNECT_MAX_MS, TRY_LATER_MIN_MS, reconnectDelay, type SocketLike,
} from "../src/lib/hintsConnection";
import type { HintServerMessage, HintsStatus } from "../src/types/hints";

const frame = (o: object) => JSON.stringify({ v: 1, ...o });
const HINT = { type: "low_confidence", hint_id: "h_0123456789abcdef", class: "cautin", confidence: 0.41, message: "Creo haber visto un cautín.", ts: 1760000000 };

// ---------------------------------------------------------------- contrato
test("parseHintMessage acepta los 3 mensajes válidos", () => {
  assert.deepEqual(parseHintMessage(frame({ type: "hello" })), { type: "hello" });
  assert.deepEqual(parseHintMessage(frame(HINT)), HINT);
  assert.deepEqual(parseHintMessage(frame({ type: "hint_cleared", hint_id: "h_0123456789abcdef" })), { type: "hint_cleared", hint_id: "h_0123456789abcdef" });
});

test("parseHintMessage rechaza lo inválido sin lanzar", () => {
  const bad: unknown[] = [
    null, 42, "", "no json", "[]", "{}", new ArrayBuffer(4), "x".repeat(5000),
    JSON.stringify({ type: "hello" }),                                  // sin versión
    JSON.stringify({ v: 2, type: "hello" }),                            // versión desconocida
    frame({ type: "otro" }),
    frame({ ...HINT, class: "martillo" }),                              // clase fuera del inventario
    frame({ ...HINT, hint_id: "../x" }),
    frame({ ...HINT, confidence: 1.5 }), frame({ ...HINT, confidence: "0.4" }),
    frame({ ...HINT, message: "" }), frame({ ...HINT, message: "a".repeat(301) }),
    frame({ ...HINT, ts: 1.5 }), frame({ ...HINT, ts: -1 }),
    frame({ type: "hint_cleared" }), frame({ type: "hint_cleared", hint_id: "corto" }),
  ];
  for (const input of bad) assert.equal(parseHintMessage(input), null, String(input).slice(0, 40));
});

// ---------------------------------------------------------------- backoff
test("reconnectDelay crece, respeta el tope y el jitter", () => {
  assert.equal(reconnectDelay(1, () => 1), 1000);
  assert.equal(reconnectDelay(2, () => 1), 2000);
  assert.equal(reconnectDelay(3, () => 1), 4000);
  assert.equal(reconnectDelay(1, () => 0), 500);
  assert.equal(reconnectDelay(50, () => 1), RECONNECT_MAX_MS);
  assert.equal(reconnectDelay(0, () => 1), 1000);
  for (let a = 1; a < 30; a++) {
    const d = reconnectDelay(a);
    assert.ok(d >= 500 && d <= RECONNECT_MAX_MS, `attempt ${a}: ${d}`);
  }
});

// ---------------------------------------------------------------- conexión
class FakeSocket implements SocketLike {
  onmessage: SocketLike["onmessage"] = null;
  onclose: SocketLike["onclose"] = null;
  onerror: SocketLike["onerror"] = null;
  closedWith: number | undefined | "never" = "never";
  constructor(public url: string) {}
  close(code?: number) { this.closedWith = code; }
  push(data: unknown) { this.onmessage?.({ data }); }
  serverClose(code: number) { this.onclose?.({ code }); }
}

function harness(opts: { fetch?: (n: number) => Promise<string>; pageProtocol?: string } = {}) {
  const sockets: FakeSocket[] = [];
  const statuses: HintsStatus[] = [];
  const messages: HintServerMessage[] = [];
  const timers = new Map<number, { fn: () => void; ms: number }>();
  let nextTimer = 1;
  let fetches = 0;
  const conn = new HintsConnection({
    fetchSocketUrl: async () => {
      fetches += 1;
      return opts.fetch ? opts.fetch(fetches) : `ws://backend/ws/hints?ticket=t${fetches}`;
    },
    createSocket: (url) => { const s = new FakeSocket(url); sockets.push(s); return s; },
    onStatus: (s) => statuses.push(s),
    onMessage: (m) => messages.push(m),
    random: () => 1,
    setTimer: (fn, ms) => { const id = nextTimer++; timers.set(id, { fn, ms }); return id; },
    clearTimer: (h) => { timers.delete(h as number); },
    pageProtocol: opts.pageProtocol ?? "http:",
  });
  const flush = async () => { for (let i = 0; i < 5; i++) await Promise.resolve(); };
  /** Dispara el temporizador pendiente más corto con ms >= min (o el único). */
  const fire = async (pick?: (t: { ms: number }) => boolean) => {
    const entry = [...timers.entries()].find(([, t]) => (pick ? pick(t) : true));
    assert.ok(entry, "no hay temporizador pendiente");
    timers.delete(entry[0]);
    entry[1].fn();
    await flush();
    return entry[1].ms;
  };
  return { conn, sockets, statuses, messages, timers, flush, fire, fetches: () => fetches };
}

test("flujo feliz: connecting -> open solo con hello; reenvía avisos", async () => {
  const h = harness();
  h.conn.start(); await h.flush();
  assert.deepEqual(h.statuses, ["connecting"]);
  assert.equal(h.sockets[0].url, "ws://backend/ws/hints?ticket=t1");
  h.sockets[0].push(frame(HINT));                       // antes del hello: se ignora
  assert.equal(h.messages.length, 0);
  h.sockets[0].push(frame({ type: "hello" }));
  assert.deepEqual(h.statuses, ["connecting", "open"]);
  h.sockets[0].push(frame(HINT));
  h.sockets[0].push("basura");
  h.sockets[0].push(frame({ type: "hint_cleared", hint_id: HINT.hint_id }));
  assert.deepEqual(h.messages.map((m) => m.type), ["low_confidence", "hint_cleared"]);
  assert.equal(h.timers.size, 0, "el temporizador de conexión se cancela con el hello");
});

test("ticket rechazado (4401 sin hello): reintenta con ticket NUEVO y backoff", async () => {
  const h = harness();
  h.conn.start(); await h.flush();
  h.sockets[0].serverClose(4401);
  assert.equal(h.statuses.at(-1), "reconnecting");
  assert.equal(await h.fire(), 1000);
  assert.equal(h.fetches(), 2);
  assert.equal(h.sockets[1].url, "ws://backend/ws/hints?ticket=t2");
  h.sockets[1].serverClose(4401);
  assert.equal(await h.fire(), 2000);                   // crece
  h.sockets[2].serverClose(4401);
  assert.equal(await h.fire(), 4000);
});

test("tras un hello el contador se reinicia", async () => {
  const h = harness();
  h.conn.start(); await h.flush();
  h.sockets[0].serverClose(4401); await h.fire();
  h.sockets[1].serverClose(4401); await h.fire();       // intento 3 pendiente de hello
  h.sockets[2].push(frame({ type: "hello" }));
  h.sockets[2].serverClose(1006);                       // se cae estando abierto
  assert.equal(await h.fire(), 1000);                   // vuelve a empezar en 1 s
  assert.equal(h.statuses.at(-1), "reconnecting");
});

test("4503 y 4403 desactivan sin reintentar", async () => {
  for (const code of [4503, 4403]) {
    const h = harness();
    h.conn.start(); await h.flush();
    h.sockets[0].push(frame({ type: "hello" }));
    h.sockets[0].serverClose(code);
    assert.equal(h.statuses.at(-1), "disabled", String(code));
    assert.equal(h.timers.size, 0);
    assert.equal(h.fetches(), 1);
  }
});

test("1013 (demasiados clientes) espera al menos 10 s", async () => {
  const h = harness();
  h.conn.start(); await h.flush();
  h.sockets[0].serverClose(1013);
  assert.ok((await h.fire()) >= TRY_LATER_MIN_MS);
});

test("el ticket con 503 desactiva; un fallo de red reintenta", async () => {
  const off = harness({ fetch: async () => { throw new HintsError("disabled", "x", 503); } });
  off.conn.start(); await off.flush();
  assert.equal(off.statuses.at(-1), "disabled");
  assert.equal(off.timers.size, 0);

  const net = harness({ fetch: async (n) => { if (n < 3) throw new HintsError("network", "x"); return "ws://b/ws/hints?ticket=ok"; } });
  net.conn.start(); await net.flush();
  assert.equal(net.statuses.at(-1), "reconnecting");
  await net.fire(); assert.equal(net.statuses.at(-1), "reconnecting");
  await net.fire();
  assert.equal(net.sockets.length, 1);
  net.sockets[0].push(frame({ type: "hello" }));
  assert.equal(net.statuses.at(-1), "open");
});

test("sin hello a tiempo se descarta el socket y se reintenta", async () => {
  const h = harness();
  h.conn.start(); await h.flush();
  assert.equal(await h.fire((t) => t.ms === CONNECT_TIMEOUT_MS), CONNECT_TIMEOUT_MS);
  assert.equal(h.sockets[0].closedWith, 1000);
  assert.equal(h.statuses.at(-1), "reconnecting");
  await h.fire();
  assert.equal(h.sockets.length, 2);
});

test("página https con ws:// se desactiva sin abrir el socket", async () => {
  const h = harness({ pageProtocol: "https:" });
  h.conn.start(); await h.flush();
  assert.equal(h.statuses.at(-1), "disabled");
  assert.equal(h.sockets.length, 0);
});

test("stop(): cierra, cancela temporizadores y silencia callbacks tardíos", async () => {
  const h = harness();
  h.conn.start(); await h.flush();
  h.sockets[0].push(frame({ type: "hello" }));
  const before = h.statuses.length;
  h.conn.stop();
  assert.equal(h.sockets[0].closedWith, 1000);
  assert.equal(h.statuses.at(-1), "idle");
  h.sockets[0].push(frame(HINT));                       // handlers ya desconectados
  h.sockets[0].serverClose(1006);
  assert.equal(h.messages.length, 0);
  assert.equal(h.timers.size, 0);
  assert.equal(h.statuses.length, before + 1);

  // stop() con reintento pendiente y con ticket en vuelo
  const h2 = harness();
  h2.conn.start(); await h2.flush();
  h2.sockets[0].serverClose(1006);
  assert.equal(h2.timers.size, 1);
  h2.conn.stop();
  assert.equal(h2.timers.size, 0);

  let release!: (u: string) => void;
  const h3 = harness({ fetch: () => new Promise<string>((r) => { release = r; }) });
  h3.conn.start(); await h3.flush();
  h3.conn.stop();
  release("ws://b/ws/hints?ticket=tarde"); await h3.flush();
  assert.equal(h3.sockets.length, 0, "un ticket que llega tras stop() no abre socket");
});

test("start() tras stop() vuelve a conectar y start() doble no duplica", async () => {
  const h = harness();
  h.conn.start(); h.conn.start(); await h.flush();
  assert.equal(h.sockets.length, 1);
  h.conn.stop(); h.conn.start(); await h.flush();
  assert.equal(h.sockets.length, 2);
});
