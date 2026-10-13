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

function readDetectPath(): string {
  const raw = process.env.BACKEND_DETECT_PATH?.trim() || "/detect";
  return raw.startsWith("/") ? raw : `/${raw}`;
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
  };
}
