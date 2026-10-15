/** Pide un ticket de un solo uso a /api/ws-ticket (proxy de Next) y devuelve la URL del WebSocket. */

export type HintsErrorCode =
  | "disabled"
  | "aborted"
  | "timeout"
  | "network"
  | "http"
  | "invalid-response"
  | "expired"
  | "rate-limited";

export class HintsError extends Error {
  constructor(
    public readonly code: HintsErrorCode,
    message: string,
    public readonly status?: number,
  ) {
    super(message);
    this.name = "HintsError";
  }
}

export interface TicketOptions {
  endpoint?: string;
  timeoutMs?: number;
  signal?: AbortSignal;
}

export async function requestHintSocketUrl(
  { endpoint = "/api/ws-ticket", timeoutMs = 8_000, signal }: TicketOptions = {},
): Promise<string> {
  if (signal?.aborted) throw new HintsError("aborted", "cancelado");
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const onExternalAbort = () => controller.abort();
  signal?.addEventListener("abort", onExternalAbort, { once: true });

  try {
    let response: Response;
    try {
      response = await fetch(endpoint, { method: "POST", cache: "no-store", signal: controller.signal });
    } catch {
      if (timedOut) throw new HintsError("timeout", "tiempo agotado");
      if (signal?.aborted) throw new HintsError("aborted", "cancelado");
      throw new HintsError("network", "sin conexión con el servidor");
    }

    if (!response.ok) {
      // 503 = avisos apagados o sin configurar en el servidor: no tiene sentido reintentar.
      if (response.status === 503) throw new HintsError("disabled", "avisos no disponibles", 503);
      throw new HintsError("http", `error ${response.status}`, response.status);
    }

    const body = (await response.json().catch(() => null)) as { url?: unknown } | null;
    const url = body?.url;
    if (typeof url !== "string" || !/^wss?:\/\//i.test(url)) {
      throw new HintsError("invalid-response", "respuesta no válida");
    }
    return url;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onExternalAbort);
  }
}

export interface FeedbackOptions {
  endpoint?: string;
  timeoutMs?: number;
  signal?: AbortSignal;
}

/**
 * Reporta que un aviso fue un falso positivo ("No es eso") a /api/hints/feedback (proxy de Next).
 * Solo viaja el hint_id: la clase y la confianza las resuelve el backend. Es idempotente por hint_id.
 * Lanza HintsError: "disabled" (feedback apagado), "expired" (el aviso ya caducó), "rate-limited", etc.
 */
export async function sendHintFalsePositive(
  hintId: string,
  { endpoint = "/api/hints/feedback", timeoutMs = 8_000, signal }: FeedbackOptions = {},
): Promise<void> {
  if (signal?.aborted) throw new HintsError("aborted", "cancelado");
  const controller = new AbortController();
  let timedOut = false;
  const timer = setTimeout(() => {
    timedOut = true;
    controller.abort();
  }, timeoutMs);
  const onExternalAbort = () => controller.abort();
  signal?.addEventListener("abort", onExternalAbort, { once: true });

  try {
    let response: Response;
    try {
      response = await fetch(endpoint, {
        method: "POST",
        cache: "no-store",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ hint_id: hintId, verdict: "false_positive" }),
        signal: controller.signal,
      });
    } catch {
      if (timedOut) throw new HintsError("timeout", "tiempo agotado");
      if (signal?.aborted) throw new HintsError("aborted", "cancelado");
      throw new HintsError("network", "sin conexión con el servidor");
    }
    if (response.ok) {
      await response.body?.cancel().catch(() => undefined);
      return;
    }
    await response.body?.cancel().catch(() => undefined);
    if (response.status === 503) throw new HintsError("disabled", "reportes no disponibles", 503);
    if (response.status === 404) throw new HintsError("expired", "el aviso ya caducó", 404);
    if (response.status === 429) throw new HintsError("rate-limited", "demasiados reportes", 429);
    throw new HintsError("http", `error ${response.status}`, response.status);
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onExternalAbort);
  }
}
