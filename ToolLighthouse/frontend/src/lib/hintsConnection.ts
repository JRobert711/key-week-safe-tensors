import { HintsError } from "@/lib/hintsApi";
import { parseHintMessage } from "@/lib/hintsContract";
import type { HintServerMessage, HintsStatus } from "@/types/hints";

/** Códigos de cierre del backend (ver hints/api.py). */
export const CLOSE_FORBIDDEN_ORIGIN = 4403;
export const CLOSE_DISABLED = 4503;
export const CLOSE_TRY_LATER = 1013;

export const RECONNECT_BASE_MS = 1_000;
export const RECONNECT_MAX_MS = 30_000;
export const TRY_LATER_MIN_MS = 10_000;
export const CONNECT_TIMEOUT_MS = 10_000;

/** Subconjunto de WebSocket que se usa aquí (permite inyectar uno falso en pruebas). */
export interface SocketLike {
  onmessage: ((event: { data: unknown }) => void) | null;
  onclose: ((event: { code: number }) => void) | null;
  onerror: (() => void) | null;
  close(code?: number): void;
}

export interface HintsConnectionOptions {
  /** Pide un ticket nuevo y devuelve la URL ws(s)://...?ticket=...; los tickets son de un solo uso. */
  fetchSocketUrl: (signal: AbortSignal) => Promise<string>;
  createSocket?: (url: string) => SocketLike;
  onStatus: (status: HintsStatus) => void;
  onMessage: (message: HintServerMessage) => void;
  random?: () => number;
  setTimer?: (fn: () => void, ms: number) => unknown;
  clearTimer?: (handle: unknown) => void;
  /** Protocolo de la página; con https un ws:// lo bloquearía el navegador (contenido mixto). */
  pageProtocol?: string;
}

/** Espera antes del intento `attempt` (1, 2, 3...): exponencial con tope y jitter entre 50 % y 100 %. */
export function reconnectDelay(attempt: number, random: () => number = Math.random): number {
  const exp = Math.min(RECONNECT_BASE_MS * 2 ** Math.max(0, Math.min(attempt - 1, 20)), RECONNECT_MAX_MS);
  return Math.round(exp * (0.5 + 0.5 * random()));
}

/**
 * Conexión resiliente al WebSocket de avisos, sin dependencias de React.
 *  - Un ticket nuevo por intento. "open" solo se declara al recibir `hello`: el backend acepta el socket
 *    y luego cierra con 4401 si el ticket es inválido, así que onopen no prueba nada.
 *  - Reconexión con backoff exponencial y jitter; el contador se reinicia con cada `hello`.
 *  - 4503 (avisos apagados) y 4403 (origen no permitido) son errores de configuración: no se reintenta.
 *  - 1013 (demasiados clientes) espera al menos 10 s.
 *  - stop() cancela ticket, temporizadores y socket; después de stop() ningún callback se dispara.
 */
export class HintsConnection {
  private readonly opts: HintsConnectionOptions;
  private active = false;
  private attempt = 0;
  private status: HintsStatus = "idle";
  private socket: SocketLike | null = null;
  private ticketAbort: AbortController | null = null;
  private retryTimer: unknown = null;
  private connectTimer: unknown = null;
  private generation = 0;

  constructor(options: HintsConnectionOptions) {
    this.opts = options;
  }

  start(): void {
    if (this.active) return;
    this.active = true;
    this.attempt = 0;
    void this.connect(++this.generation);
  }

  stop(): void {
    this.active = false;
    this.generation += 1;
    this.clearTimers();
    this.ticketAbort?.abort();
    this.ticketAbort = null;
    this.detachSocket(1000);
    this.setStatus("idle");
  }

  // ------------------------------------------------------------------ interno
  private setTimer(fn: () => void, ms: number): unknown {
    return (this.opts.setTimer ?? ((f, m) => setTimeout(f, m)))(fn, ms);
  }

  private clearTimer(handle: unknown): void {
    if (handle === null) return;
    (this.opts.clearTimer ?? ((h) => clearTimeout(h as ReturnType<typeof setTimeout>)))(handle);
  }

  private clearTimers(): void {
    this.clearTimer(this.retryTimer);
    this.clearTimer(this.connectTimer);
    this.retryTimer = null;
    this.connectTimer = null;
  }

  private setStatus(status: HintsStatus): void {
    if (this.status === status) return;
    this.status = status;
    this.opts.onStatus(status);
  }

  private detachSocket(closeCode?: number): void {
    const socket = this.socket;
    this.socket = null;
    if (!socket) return;
    socket.onmessage = null;
    socket.onclose = null;
    socket.onerror = null;
    try {
      socket.close(closeCode);
    } catch {
      /* ya estaba cerrado */
    }
  }

  private disable(reason: string): void {
    this.active = false;
    this.generation += 1;
    this.clearTimers();
    this.detachSocket(1000);
    console.warn(`[hints] avisos desactivados: ${reason}`);
    this.setStatus("disabled");
  }

  private scheduleRetry(generation: number, minDelayMs = 0): void {
    if (!this.active || generation !== this.generation) return;
    this.attempt += 1;
    const delay = Math.max(minDelayMs, reconnectDelay(this.attempt, this.opts.random));
    this.setStatus("reconnecting");
    this.clearTimer(this.retryTimer);
    this.retryTimer = this.setTimer(() => {
      this.retryTimer = null;
      void this.connect(generation);
    }, delay);
  }

  private async connect(generation: number): Promise<void> {
    if (!this.active || generation !== this.generation) return;
    this.setStatus(this.attempt === 0 ? "connecting" : "reconnecting");

    const controller = new AbortController();
    this.ticketAbort = controller;
    let url: string;
    try {
      url = await this.opts.fetchSocketUrl(controller.signal);
    } catch (error) {
      if (!this.active || generation !== this.generation) return;
      if (error instanceof HintsError && error.code === "disabled") {
        this.disable("el servidor no tiene avisos disponibles");
        return;
      }
      this.scheduleRetry(generation);
      return;
    } finally {
      if (this.ticketAbort === controller) this.ticketAbort = null;
    }
    if (!this.active || generation !== this.generation) return;

    const protocol = this.opts.pageProtocol ?? (typeof window !== "undefined" ? window.location.protocol : "");
    if (protocol === "https:" && /^ws:\/\//i.test(url)) {
      this.disable("la página usa https pero el WebSocket es ws:// (define WS_PUBLIC_URL con wss://)");
      return;
    }

    let socket: SocketLike;
    try {
      socket = (this.opts.createSocket ?? ((u) => new WebSocket(u) as unknown as SocketLike))(url);
    } catch {
      this.scheduleRetry(generation);
      return;
    }
    this.socket = socket;

    // Si no llega `hello` a tiempo, se descarta el socket y se reintenta con otro ticket.
    this.connectTimer = this.setTimer(() => {
      this.connectTimer = null;
      if (this.socket !== socket) return;
      this.detachSocket(1000);
      this.scheduleRetry(generation);
    }, CONNECT_TIMEOUT_MS);

    socket.onmessage = (event) => {
      if (this.socket !== socket || !this.active || generation !== this.generation) return;
      const message = parseHintMessage(event.data);
      if (!message) return;
      if (message.type === "hello") {
        this.clearTimer(this.connectTimer);
        this.connectTimer = null;
        this.attempt = 0;
        this.setStatus("open");
        return;
      }
      if (this.status !== "open") return; // un aviso antes del hello no es de fiar
      this.opts.onMessage(message);
    };
    socket.onerror = () => {
      /* el cierre llega después y se gestiona en onclose */
    };
    socket.onclose = (event) => {
      if (this.socket !== socket || !this.active || generation !== this.generation) return;
      this.socket = null;
      this.clearTimer(this.connectTimer);
      this.connectTimer = null;
      if (event.code === CLOSE_DISABLED) return this.disable("avisos deshabilitados en el backend");
      if (event.code === CLOSE_FORBIDDEN_ORIGIN) {
        return this.disable("el backend rechazó el origen (revisa WS_ALLOWED_ORIGINS)");
      }
      this.scheduleRetry(generation, event.code === CLOSE_TRY_LATER ? TRY_LATER_MIN_MS : 0);
    };
  }
}
