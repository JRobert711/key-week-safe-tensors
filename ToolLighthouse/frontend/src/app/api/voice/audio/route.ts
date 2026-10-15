import { NextResponse } from "next/server";
import { isAudioContentType, parseVoiceRequest } from "@/lib/voiceContract";
import { getServerConfig } from "@/lib/server-config";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const MAX_REQUEST_BYTES = 2048;
const MAX_RETRY_AFTER_SECONDS = 300;
const AUDIO_HEADERS = ["x-voice-event-id", "x-voice-transition-id", "x-voice-hint-id", "x-voice-kind", "x-voice-cache"] as const;

function fail(status: number, error: string, message: string, extraHeaders?: Record<string, string>) {
  const headers = new Headers({ "Cache-Control": "no-store" });
  for (const [name, value] of Object.entries(extraHeaders ?? {})) headers.set(name, value);
  return NextResponse.json({ error, message }, { status, headers });
}

async function readBoundedRequestBody(request: Request, maxBytes: number): Promise<Uint8Array | null> {
  if (!request.body) return new Uint8Array();
  const reader = request.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > maxBytes) {
        await reader.cancel().catch(() => undefined);
        return null;
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const merged = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    merged.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return merged;
}

async function readBoundedBody(response: Response, maxBytes: number): Promise<ArrayBuffer | null> {
  const declared = Number(response.headers.get("content-length"));
  if (Number.isFinite(declared) && declared > maxBytes) {
    await response.body?.cancel().catch(() => undefined);
    return null;
  }
  if (!response.body) return new ArrayBuffer(0);
  const reader = response.body.getReader();
  const chunks: Uint8Array[] = [];
  let total = 0;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      total += value.byteLength;
      if (total > maxBytes) {
        await reader.cancel().catch(() => undefined);
        return null;
      }
      chunks.push(value);
    }
  } finally {
    reader.releaseLock();
  }
  const merged = new Uint8Array(total);
  let offset = 0;
  for (const chunk of chunks) {
    merged.set(chunk, offset);
    offset += chunk.byteLength;
  }
  return merged.buffer;
}

function mapUpstreamStatus(status: number, code: string | null): number {
  if (status === 409 && code === "stale_event") return 409;
  if (status === 404 && code === "unknown_hint") return 404;
  if (status === 413) return 413;
  if (status === 429) return 429;
  if (status === 503 && (code === "voice_disabled" || code === "hints_disabled")) return 503;
  if (status === 504 && code === "provider_timeout") return 504;
  if (status === 400 && code === "invalid_request") return 400;
  return 502;
}

function publicMessage(status: number): string {
  switch (status) {
    case 400: return "La solicitud de voz no es válida.";
    case 404: return "El aviso ya no está disponible.";
    case 409: return "La solicitud de voz quedó obsoleta.";
    case 413: return "La solicitud de voz es demasiado grande.";
    case 429: return "La voz está ocupada. Espera un momento y vuelve a intentarlo.";
    case 503: return "El servicio de voz no está disponible.";
    case 504: return "La generación de voz tardó demasiado. Inténtalo de nuevo.";
    default: return "El servicio de voz no pudo procesar la solicitud.";
  }
}

export async function POST(request: Request) {
  let config: ReturnType<typeof getServerConfig>;
  try {
    config = getServerConfig();
  } catch (error) {
    console.error("[voice-proxy] configuración inválida:", error instanceof Error ? error.message : "desconocido");
    return fail(500, "server-config", "El servidor no está configurado correctamente.");
  }

  const contentType = request.headers.get("content-type")?.toLowerCase() ?? "";
  if (!contentType.startsWith("application/json")) {
    return fail(415, "unsupported-media-type", "Se esperaba una solicitud JSON.");
  }

  const declaredLength = Number(request.headers.get("content-length"));
  if (Number.isFinite(declaredLength) && declaredLength > MAX_REQUEST_BYTES) {
    return fail(413, "too-large", "La solicitud de voz es demasiado grande.");
  }

  let rawBody: string;
  try {
    const bytes = await readBoundedRequestBody(request, MAX_REQUEST_BYTES);
    if (bytes === null) return fail(413, "too-large", "La solicitud de voz es demasiado grande.");
    rawBody = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
  } catch {
    return fail(400, "invalid-body", "No se pudo leer la solicitud de voz.");
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(rawBody);
  } catch {
    return fail(400, "invalid-body", "La solicitud de voz no contiene JSON válido.");
  }
  const event = parseVoiceRequest(parsed);
  if (!event) return fail(400, "invalid-event", "La solicitud de voz no cumple el contrato esperado.");
  if (!config.backendUrl) return fail(500, "server-config", "El servidor no está configurado correctamente.");

  const headers = new Headers({ "Content-Type": "application/json", Accept: "audio/*, application/json" });
  if (config.voiceBackendKey) headers.set("X-Backend-Key", config.voiceBackendKey);

  const signal = AbortSignal.any([request.signal, AbortSignal.timeout(config.voiceBackendTimeoutMs)]);
  let upstream: Response;
  try {
    upstream = await fetch(`${config.backendUrl}${config.voiceAudioPath}`, {
      method: "POST",
      headers,
      body: JSON.stringify(event),
      signal,
      cache: "no-store",
      redirect: "error",
    });
  } catch (error) {
    if (request.signal.aborted) return fail(499, "aborted", "La solicitud de voz se canceló.");
    if (signal.aborted) return fail(504, "upstream-timeout", "El servicio de voz tardó demasiado en responder.");
    console.error("[voice-proxy] conexión ascendente fallida:", error instanceof Error ? error.name : "desconocido");
    return fail(502, "upstream-unreachable", "No se pudo conectar con el servicio de voz.");
  }

  if (!upstream.ok) {
    let code: string | null = null;
    if (upstream.status === 401 || upstream.status === 403) {
      console.error("[voice-proxy] autenticación del backend de voz rechazada (revisa VOICE_BACKEND_KEY).");
    } else {
      const body = await upstream.json().catch(() => null) as { error?: { code?: unknown } } | null;
      if (typeof body?.error?.code === "string") code = body.error.code;
    }
    await upstream.body?.cancel().catch(() => undefined);
    const status = mapUpstreamStatus(upstream.status, code);
    const extra: Record<string, string> = {};
    if (status === 429) {
      const retry = Number(upstream.headers.get("retry-after"));
      if (Number.isInteger(retry) && retry > 0) extra["Retry-After"] = String(Math.min(retry, MAX_RETRY_AFTER_SECONDS));
    }
    return fail(status, "upstream-error", publicMessage(status), extra);
  }

  const contentTypeUpstream = upstream.headers.get("content-type") ?? "";
  if (!isAudioContentType(contentTypeUpstream)) {
    await upstream.body?.cancel().catch(() => undefined);
    return fail(502, "upstream-invalid", "El servicio de voz devolvió un formato de audio no válido.");
  }

  let audio: ArrayBuffer | null;
  try {
    audio = await readBoundedBody(upstream, config.maxVoiceAudioBytes);
  } catch (error) {
    if (request.signal.aborted) return fail(499, "aborted", "La solicitud de voz se canceló.");
    if (signal.aborted) return fail(504, "upstream-timeout", "El servicio de voz tardó demasiado en responder.");
    console.error("[voice-proxy] lectura de audio fallida:", error instanceof Error ? error.name : "desconocido");
    return fail(502, "upstream-unreachable", "No se pudo leer el audio generado.");
  }
  if (audio === null) return fail(502, "upstream-invalid", "El audio generado supera el tamaño permitido.");
  if (audio.byteLength === 0) return fail(502, "upstream-invalid", "El servicio de voz devolvió un audio vacío.");

  const responseHeaders = new Headers({
    "Content-Type": contentTypeUpstream,
    "Cache-Control": "no-store",
    "X-Content-Type-Options": "nosniff",
  });
  for (const name of AUDIO_HEADERS) {
    const value = upstream.headers.get(name);
    if (value) responseHeaders.set(name, value);
  }
  return new Response(audio, { status: 200, headers: responseHeaders });
}
