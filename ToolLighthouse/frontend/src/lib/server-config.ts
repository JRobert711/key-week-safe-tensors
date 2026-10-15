import "server-only";

/**
 * Configuración que solo existe en el servidor.
 * Nunca importar este módulo desde un componente cliente.
 */
function readInt(name: string, fallback: number): number {
  const raw = process.env[name];
  const value = raw ? Number.parseInt(raw, 10) : NaN;
  return Number.isFinite(value) && value > 0 ? value : fallback;
}

/**
 * Si es true, el navegador puede indicar la URL del backend en cada envío.
 * Solo para pruebas locales: convierte el proxy en un relé hacia cualquier URL.
 */
export function isClientBackendUrlEnabled(): boolean {
  return process.env.ALLOW_CLIENT_BACKEND_URL === "true";
}

function readBackendUrl(required: boolean): string | null {
  const raw = process.env.BACKEND_URL?.trim();
  if (!raw) {
    if (required) throw new Error("BACKEND_URL no está definida. Copia .env.example a .env.local.");
    return null;
  }
  let parsed: URL;
  try {
    parsed = new URL(raw);
  } catch {
    throw new Error("BACKEND_URL no es una URL válida.");
  }
  if (parsed.protocol !== "http:" && parsed.protocol !== "https:") {
    throw new Error("BACKEND_URL debe usar http:// o https://.");
  }
  return raw.replace(/\/+$/, "");
}

function readPath(name: string, fallback: string): string {
  const raw = process.env[name]?.trim() || fallback;
  return raw.startsWith("/") ? raw : `/${raw}`;
}

function readDetectPath(): string {
  return readPath("BACKEND_DETECT_PATH", "/detect");
}

function readVoiceAudioPath(): string {
  return readPath("VOICE_AUDIO_PATH", "/voice/audio");
}

export function getServerConfig() {
  const allowClientBackendUrl = isClientBackendUrlEnabled();
  return {
    /** null solo si BACKEND_URL no está definida y la URL se escribe desde el navegador. */
    backendUrl: readBackendUrl(!allowClientBackendUrl),
    allowClientBackendUrl,
    /** Ruta del backend que recibe la imagen (contrato: POST /detect). */
    detectPath: readDetectPath(),
    maxUploadBytes: readInt("MAX_UPLOAD_BYTES", 1_048_576),
    backendTimeoutMs: readInt("BACKEND_TIMEOUT_MS", 10_000),
    /** Ruta interna de síntesis; nunca se expone al navegador. */
    voiceAudioPath: readVoiceAudioPath(),
    /** Clave opcional para autenticar el proxy ante FastAPI; solo servidor. */
    voiceBackendKey: process.env.VOICE_BACKEND_KEY?.trim() || "",
    voiceBackendTimeoutMs: readInt("VOICE_BACKEND_TIMEOUT_MS", 35_000),
    maxVoiceAudioBytes: readInt("VOICE_MAX_AUDIO_BYTES", 500_000),
  };
}

/**
 * Configuración de los avisos de baja confianza (WebSocket). Va aparte de getServerConfig() a propósito:
 * un valor inválido aquí solo apaga los avisos y nunca rompe /api/capture ni /api/voice/audio.
 */
function readHintWsBase(backendUrl: string | null): string | null {
  const raw = process.env.WS_PUBLIC_URL?.trim();
  if (raw) {
    let parsed: URL;
    try {
      parsed = new URL(raw);
    } catch {
      throw new Error("WS_PUBLIC_URL no es una URL válida.");
    }
    const protocol = { "ws:": "ws:", "wss:": "wss:", "http:": "ws:", "https:": "wss:" }[parsed.protocol];
    if (!protocol) throw new Error("WS_PUBLIC_URL debe usar ws://, wss://, http:// o https://.");
    return `${protocol}//${parsed.host}${parsed.pathname.replace(/\/+$/, "")}`;
  }
  // Sin WS_PUBLIC_URL se asume que el navegador alcanza el mismo host que BACKEND_URL (despliegue local).
  if (!backendUrl) return null;
  const parsed = new URL(backendUrl);
  return `${parsed.protocol === "https:" ? "wss:" : "ws:"}//${parsed.host}${parsed.pathname.replace(/\/+$/, "")}`;
}

export function getHintsServerConfig() {
  const backendUrl = readBackendUrl(false);
  return {
    backendUrl,
    /** Base pública del WebSocket (ws:// o wss://), sin la ruta. null = no se puede construir. */
    wsBase: readHintWsBase(backendUrl),
    wsPath: readPath("HINT_WS_PATH", "/ws/hints"),
    ticketPath: readPath("HINT_TICKET_PATH", "/hints/ticket"),
    /** Ruta interna del backend que recibe el reporte "No es eso" (POST /hints/feedback). */
    feedbackPath: readPath("HINT_FEEDBACK_PATH", "/hints/feedback"),
    feedbackTimeoutMs: readInt("HINT_FEEDBACK_TIMEOUT_MS", 5_000),
    /** Misma credencial que la voz salvo que se defina una propia para los avisos. */
    backendKey: process.env.HINT_BACKEND_KEY?.trim() || process.env.VOICE_BACKEND_KEY?.trim() || "",
    ticketTimeoutMs: readInt("HINT_TICKET_TIMEOUT_MS", 5_000),
  };
}
