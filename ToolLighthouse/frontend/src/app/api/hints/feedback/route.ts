import { NextResponse } from "next/server";
import { getHintsServerConfig } from "@/lib/server-config";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const MAX_REQUEST_BYTES = 512;
const MAX_RETRY_AFTER_SECONDS = 300;
const ID_PATTERN = /^[A-Za-z0-9_-]{8,64}$/;

function fail(status: number, error: string, message: string, extraHeaders?: Record<string, string>) {
  const headers = new Headers({ "Cache-Control": "no-store" });
  for (const [name, value] of Object.entries(extraHeaders ?? {})) headers.set(name, value);
  return NextResponse.json({ error, message }, { status, headers });
}

async function readBoundedText(request: Request, maxBytes: number): Promise<string | null> {
  if (!request.body) return "";
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
  return new TextDecoder().decode(Buffer.concat(chunks));
}

/**
 * Reenvía el reporte "No es eso" a POST /hints/feedback del backend con X-Backend-Key (solo servidor).
 * Del navegador solo se acepta {hint_id, verdict:"false_positive"}; no se reenvía nada más.
 * La clase y la confianza las toma el backend de su registro de avisos, no del cliente.
 */
export async function POST(request: Request) {
  const site = request.headers.get("sec-fetch-site");
  if (site && site !== "same-origin" && site !== "none") {
    return fail(403, "forbidden", "Origen no permitido.");
  }
  if (!(request.headers.get("content-type") ?? "").toLowerCase().startsWith("application/json")) {
    return fail(415, "unsupported-media-type", "Content-Type debe ser application/json.");
  }

  const text = await readBoundedText(request, MAX_REQUEST_BYTES);
  if (text === null) return fail(413, "payload-too-large", "El cuerpo es demasiado grande.");

  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return fail(400, "invalid-body", "El cuerpo no es JSON válido.");
  }
  const value = parsed as Record<string, unknown> | null;
  if (
    typeof value !== "object" ||
    value === null ||
    Array.isArray(value) ||
    Object.keys(value).some((key) => key !== "hint_id" && key !== "verdict") ||
    typeof value.hint_id !== "string" ||
    !ID_PATTERN.test(value.hint_id) ||
    value.verdict !== "false_positive"
  ) {
    return fail(400, "invalid-body", "Se espera {hint_id, verdict}.");
  }

  let config: ReturnType<typeof getHintsServerConfig>;
  try {
    config = getHintsServerConfig();
  } catch (error) {
    console.error("[hints-feedback] configuración inválida:", error instanceof Error ? error.message : "desconocido");
    return fail(503, "feedback-disabled", "Los reportes no están configurados correctamente.");
  }
  if (!config.backendUrl) return fail(503, "feedback-disabled", "Los reportes requieren BACKEND_URL en el servidor.");

  const headers = new Headers({ Accept: "application/json", "Content-Type": "application/json" });
  if (config.backendKey) headers.set("X-Backend-Key", config.backendKey);

  const signal = AbortSignal.any([request.signal, AbortSignal.timeout(config.feedbackTimeoutMs)]);
  let upstream: Response;
  try {
    upstream = await fetch(`${config.backendUrl}${config.feedbackPath}`, {
      method: "POST",
      headers,
      body: JSON.stringify({ hint_id: value.hint_id, verdict: "false_positive" }),
      signal,
      cache: "no-store",
      redirect: "error",
    });
  } catch (error) {
    if (request.signal.aborted) return fail(499, "aborted", "La solicitud se canceló.");
    if (signal.aborted) return fail(504, "upstream-timeout", "El backend tardó demasiado en responder.");
    console.error("[hints-feedback] conexión ascendente fallida:", error instanceof Error ? error.name : "desconocido");
    return fail(502, "upstream-unreachable", "No se pudo conectar con el backend.");
  }

  if (upstream.ok) {
    const body = (await upstream.json().catch(() => null)) as { already?: unknown; muted_seconds?: unknown } | null;
    return NextResponse.json(
      {
        ok: true,
        already: body?.already === true,
        muted_seconds: typeof body?.muted_seconds === "number" ? body.muted_seconds : 0,
      },
      { headers: { "Cache-Control": "no-store" } },
    );
  }

  let code: string | null = null;
  if (upstream.status === 401 || upstream.status === 403) {
    console.error("[hints-feedback] el backend rechazó la credencial (revisa HINT_BACKEND_KEY / VOICE_BACKEND_KEY).");
  } else {
    const body = (await upstream.json().catch(() => null)) as { error?: { code?: unknown } } | null;
    if (typeof body?.error?.code === "string") code = body.error.code;
  }
  const retryAfter = Number(upstream.headers.get("retry-after"));
  await upstream.body?.cancel().catch(() => undefined);

  if (upstream.status === 404 && code === "unknown_hint") {
    return fail(404, "unknown-hint", "El aviso ya caducó.");
  }
  if (upstream.status === 429) {
    const seconds = Number.isFinite(retryAfter) && retryAfter > 0 ? Math.min(Math.ceil(retryAfter), MAX_RETRY_AFTER_SECONDS) : 30;
    return fail(429, "rate-limited", "Demasiados reportes seguidos.", { "Retry-After": String(seconds) });
  }
  if (upstream.status === 503 && code === "feedback_disabled") {
    return fail(503, "feedback-disabled", "Los reportes están deshabilitados en el backend.");
  }
  return fail(502, "upstream-error", "El backend no pudo registrar el reporte.");
}
