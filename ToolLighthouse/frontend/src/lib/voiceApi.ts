import { isAudioContentType, parseVoiceEvent } from "@/lib/voiceContract";
import type { VoiceEvent } from "@/types/voice";

export type VoiceErrorCode =
  | "invalid-event"
  | "timeout"
  | "aborted"
  | "network"
  | "http"
  | "invalid-response";

/** Error HTTP de voz con un mensaje seguro y apto para mostrar en la interfaz. */
export class VoiceError extends Error {
  constructor(
    public readonly code: VoiceErrorCode,
    public readonly userMessage: string,
    public readonly status?: number,
    public readonly retryAfterSeconds?: number,
  ) {
    super(userMessage);
    this.name = "VoiceError";
  }
}

export interface VoiceRequestOptions {
  endpoint?: string;
  /** Debe superar el timeout del proxy (35 s por defecto). */
  timeoutMs?: number;
  signal?: AbortSignal;
}

function httpMessage(status: number): string {
  switch (status) {
    case 400: return "La solicitud de voz no es válida.";
    case 409: return "La solicitud de voz quedó obsoleta. Vuelve a probar.";
    case 413: return "La solicitud de voz es demasiado grande.";
    case 429: return "La voz está ocupada. Espera un momento y vuelve a intentarlo.";
    case 503: return "La voz no está disponible. Revisa la configuración del backend.";
    case 504: return "La generación de voz tardó demasiado. Vuelve a intentarlo.";
    case 502: return "No se pudo conectar con el servicio de voz.";
    default: return `El servidor respondió con un error (${status}). Vuelve a intentarlo.`;
  }
}

/** Solicita audio al proxy same-origin; la URL y la clave del backend no llegan al navegador. */
export async function requestVoiceAudio(
  event: VoiceEvent,
  { endpoint = "/api/voice/audio", timeoutMs = 45_000, signal }: VoiceRequestOptions = {},
): Promise<Blob> {
  if (!parseVoiceEvent(event)) throw new VoiceError("invalid-event", "La solicitud de voz no es válida.");
  if (signal?.aborted) throw new VoiceError("aborted", "La solicitud de voz se canceló.");

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
        headers: { "Content-Type": "application/json", Accept: "audio/*, application/json" },
        body: JSON.stringify(event),
        signal: controller.signal,
        cache: "no-store",
      });
    } catch {
      if (timedOut) throw new VoiceError("timeout", "La solicitud de voz tardó demasiado en responder.");
      if (signal?.aborted) throw new VoiceError("aborted", "La solicitud de voz se canceló.");
      throw new VoiceError("network", "No se pudo conectar con el servidor. Revisa tu conexión.");
    }

    if (!response.ok) {
      const retryHeader = Number(response.headers.get("retry-after"));
      const retryAfter = Number.isInteger(retryHeader) && retryHeader > 0 ? retryHeader : undefined;
      throw new VoiceError("http", httpMessage(response.status), response.status, retryAfter);
    }

    const contentType = response.headers.get("content-type");
    if (!isAudioContentType(contentType)) {
      await response.body?.cancel().catch(() => undefined);
      throw new VoiceError("invalid-response", "El servidor no devolvió un archivo de audio válido.");
    }

    const blob = await response.blob();
    if (blob.size === 0) throw new VoiceError("invalid-response", "El servicio de voz devolvió un audio vacío.");
    return blob;
  } finally {
    clearTimeout(timer);
    signal?.removeEventListener("abort", onExternalAbort);
  }
}
