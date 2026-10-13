import type { DetectionResult } from "@/types/detection";

/** Servicio HTTP: envía un fotograma a /api/capture. No depende de React ni del DOM. */

export type SendErrorCode =
  | "invalid-image"
  | "timeout"
  | "aborted"
  | "network"
  | "http"
  | "invalid-response";

export class SendError extends Error {
  constructor(
    public readonly code: SendErrorCode,
    public readonly userMessage: string,
    public readonly status?: number,
  ) {
    super(userMessage);
    this.name = "SendError";
  }
}

export interface SendOptions {
  endpoint?: string;
  /** Debe ser mayor que el timeout del servidor (10 s) para ver su respuesta 504. */
  timeoutMs?: number;
  signal?: AbortSignal;
  /** URL del backend escrita por el usuario. El servidor la ignora si no está habilitada. */
  backendUrl?: string;
}

function httpMessage(status: number): string {
  switch (status) {
    case 400:
      return "El servidor no aceptó la imagen. Captura otra vez.";
    case 413:
      return "La imagen es demasiado grande para el servidor.";
    case 502:
      return "El servicio de detección no está disponible. Inténtalo en unos minutos.";
    case 504:
      return "El servicio de detección tardó demasiado en responder. Vuelve a intentarlo.";
    default:
      return `El servidor respondió con un error (${status}). Vuelve a intentarlo.`;
  }
}

/** Mensajes del propio proxy que sí conviene mostrar tal cual (no contienen datos privados). */
const PROXY_MESSAGE_CODES = new Set(["invalid-backend-url", "missing-backend-url"]);

async function errorMessage(response: Response): Promise<string> {
  if (response.status === 400) {
    const body = (await response.json().catch(() => null)) as { error?: unknown; message?: unknown } | null;
    if (typeof body?.error === "string" && PROXY_MESSAGE_CODES.has(body.error) && typeof body.message === "string") {
      return body.message;
    }
  }
  return httpMessage(response.status);
}

function parseResult(data: unknown): DetectionResult | null {
  if (typeof data !== "object" || data === null) return null;
  const { class: cls, confidence, detections } = data as Record<string, unknown>;
  if (cls !== null && typeof cls !== "string") return null;
  if (confidence !== null && typeof confidence !== "number") return null;
  if (typeof detections !== "number") return null;
  return { class: cls, confidence, detections };
}

export async function sendFrame(
  frame: Blob,
  { endpoint = "/api/capture", timeoutMs = 15_000, signal, backendUrl }: SendOptions = {},
): Promise<DetectionResult> {
  if (frame.type !== "image/jpeg" || frame.size === 0) {
    throw new SendError("invalid-image", "La imagen capturada no es válida.");
  }
  if (signal?.aborted) throw new SendError("aborted", "El envío se canceló.");

  const body = new FormData();
  body.append("image", frame, "frame.jpg");
  if (backendUrl?.trim()) body.append("backend_url", backendUrl.trim());

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
    let data: unknown;
    try {
      response = await fetch(endpoint, { method: "POST", body, signal: controller.signal });
      if (!response.ok) {
        throw new SendError("http", await errorMessage(response), response.status);
      }
      data = await response.json().catch(() => undefined);
    } catch (error) {
      if (error instanceof SendError) throw error;
      if (timedOut) {
        throw new SendError("timeout", "El servidor tardó demasiado en responder. Vuelve a intentarlo.");
      }
      if (signal?.aborted) throw new SendError("aborted", "El envío se canceló.");
      throw new SendError("network", "No se pudo conectar con el servidor. Revisa tu conexión.");
    }

    const result = parseResult(data);
    if (!result) {
      throw new SendError("invalid-response", "El servidor devolvió una respuesta que no se entiende.");
    }
    return result;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onExternalAbort);
  }
}
