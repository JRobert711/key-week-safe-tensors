import { NextResponse } from "next/server";
import { getHintsServerConfig } from "@/lib/server-config";

export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const TICKET_PATTERN = /^[A-Za-z0-9_-]{16,256}$/;

function fail(status: number, error: string, message: string) {
  return NextResponse.json({ error, message }, { status, headers: { "Cache-Control": "no-store" } });
}

/**
 * Entrega al navegador la URL de un WebSocket de avisos con un ticket de un solo uso.
 * La clave X-Backend-Key solo existe aquí (servidor); el navegador nunca la ve.
 * 503 = avisos no disponibles de forma estable (el cliente deja de reintentar).
 */
export async function POST(request: Request) {
  // Defensa barata contra otros sitios: un navegador moderno declara el origen de la petición.
  const site = request.headers.get("sec-fetch-site");
  if (site && site !== "same-origin" && site !== "none") {
    return fail(403, "forbidden", "Origen no permitido.");
  }

  let config: ReturnType<typeof getHintsServerConfig>;
  try {
    config = getHintsServerConfig();
  } catch (error) {
    console.error("[hints-proxy] configuración inválida:", error instanceof Error ? error.message : "desconocido");
    return fail(503, "hints-disabled", "Los avisos no están configurados correctamente.");
  }
  if (!config.backendUrl || !config.wsBase) {
    return fail(503, "hints-disabled", "Los avisos requieren BACKEND_URL en el servidor.");
  }

  const headers = new Headers({ Accept: "application/json" });
  if (config.backendKey) headers.set("X-Backend-Key", config.backendKey);

  const signal = AbortSignal.any([request.signal, AbortSignal.timeout(config.ticketTimeoutMs)]);
  let upstream: Response;
  try {
    upstream = await fetch(`${config.backendUrl}${config.ticketPath}`, {
      method: "POST",
      headers,
      signal,
      cache: "no-store",
      redirect: "error",
    });
  } catch (error) {
    if (request.signal.aborted) return fail(499, "aborted", "La solicitud se canceló.");
    if (signal.aborted) return fail(504, "upstream-timeout", "El backend tardó demasiado en responder.");
    console.error("[hints-proxy] conexión ascendente fallida:", error instanceof Error ? error.name : "desconocido");
    return fail(502, "upstream-unreachable", "No se pudo conectar con el backend.");
  }

  if (!upstream.ok) {
    let code: string | null = null;
    if (upstream.status === 401 || upstream.status === 403) {
      console.error("[hints-proxy] el backend rechazó la credencial (revisa HINT_BACKEND_KEY / VOICE_BACKEND_KEY).");
    } else {
      const body = (await upstream.json().catch(() => null)) as { error?: { code?: unknown } } | null;
      if (typeof body?.error?.code === "string") code = body.error.code;
    }
    await upstream.body?.cancel().catch(() => undefined);
    if (upstream.status === 503 && code === "hints_disabled") {
      return fail(503, "hints-disabled", "Los avisos están deshabilitados en el backend.");
    }
    return fail(502, "upstream-error", "El backend no pudo emitir el ticket.");
  }

  const body = (await upstream.json().catch(() => null)) as { ticket?: unknown; expires_in?: unknown } | null;
  const ticket = body?.ticket;
  if (typeof ticket !== "string" || !TICKET_PATTERN.test(ticket)) {
    return fail(502, "upstream-invalid", "El backend devolvió un ticket no válido.");
  }

  const url = `${config.wsBase}${config.wsPath}?ticket=${encodeURIComponent(ticket)}`;
  return NextResponse.json(
    { url, expires_in: typeof body?.expires_in === "number" ? body.expires_in : null },
    { headers: { "Cache-Control": "no-store" } },
  );
}
