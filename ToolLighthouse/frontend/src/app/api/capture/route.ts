import { NextResponse } from "next/server";
import { parseDetectionResult } from "@/lib/detectionContract";
import { getServerConfig, isClientBackendUrlEnabled } from "@/lib/server-config";

/**
 * Proxy: el navegador llama a POST /api/capture y este handler reenvía la imagen
 * al backend. La URL del backend solo existe aquí (servidor) y nunca se devuelve.
 *
 * Contrato con el cliente (src/lib/captureApi.ts):
 *   entrada : multipart/form-data con el campo "image" (JPEG)
 *   salida  : { class, confidence, detections, boxes?, model? } o { error, message } con estado HTTP
 *            (src/lib/detectionContract.ts valida y descarta cualquier otro campo)
 * GET: solo si ALLOW_CLIENT_BACKEND_URL=true, muestra una página oculta (no enlazada desde
 * la app) para pegar la URL del backend y probar la conexión. Si no, responde 405.
 * Otros métodos HTTP no están exportados: Next.js responde 405 automáticamente.
 */
export const runtime = "nodejs";
export const dynamic = "force-dynamic";

const FIELD = "image";
const BACKEND_URL_FIELD = "backend_url";
const MAX_BACKEND_URL_LENGTH = 2048;
// Margen para las cabeceras multipart que rodean al archivo.
const MULTIPART_OVERHEAD_BYTES = 16 * 1024;

function fail(status: number, error: string, message: string) {
  return NextResponse.json({ error, message }, { status, headers: { "Cache-Control": "no-store" } });
}

function isJpeg(buffer: ArrayBuffer): boolean {
  if (buffer.byteLength < 3) return false;
  const [a, b, c] = new Uint8Array(buffer, 0, 3);
  return a === 0xff && b === 0xd8 && c === 0xff;
}

/**
 * Solo se usa si ALLOW_CLIENT_BACKEND_URL=true. Acepta http(s) sin credenciales.
 * Si la URL pegada ya trae una ruta (p. ej. http://host:8000/detect) se usa tal cual;
 * si solo trae el host, se le añade BACKEND_DETECT_PATH.
 */
function parseClientBackendUrl(raw: string, detectPath: string): string | null {
  const value = raw.trim();
  if (value.length === 0 || value.length > MAX_BACKEND_URL_LENGTH) return null;
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    return null;
  }
  if (url.protocol !== "http:" && url.protocol !== "https:") return null;
  if (url.username || url.password) return null;
  url.hash = "";
  return url.pathname === "/" && !url.search ? `${url.origin}${detectPath}` : url.toString();
}

function upstreamErrorStatus(status: number): number {
  if (status === 413) return 413;
  if (status === 504) return 504;
  if (status >= 400 && status < 500) return 400;
  return 502;
}

const CONFIG_PAGE = `<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>ToolLighthouse · Backend</title>
<style>
:root{color-scheme:dark;--bg:#0e1a24;--surface:#16283a;--text:#eaf1f6;--muted:#9fb3c4;--beam:#ffc94d;--line:#2a4258}
*{box-sizing:border-box}
body{margin:0;min-height:100vh;background:var(--bg);color:var(--text);font:16px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,sans-serif}
main{max-width:34rem;margin:0 auto;padding:2.5rem 1.25rem}
h1{margin:0 0 .5rem;font-size:1.75rem;border-left:.35rem solid var(--beam);padding-left:.75rem}
p{color:var(--muted);margin:.25rem 0 1.25rem}
label{display:grid;gap:.35rem;font-size:.9rem;color:var(--muted)}
input,button{font:inherit;color:var(--text);background:var(--surface);border:1px solid var(--line);border-radius:.375rem;padding:.6rem .75rem;min-height:2.75rem}
input{width:100%}
input:focus-visible,button:focus-visible{outline:2px solid var(--beam);outline-offset:1px}
.row{display:flex;flex-wrap:wrap;gap:.75rem;margin-top:1rem}
button{cursor:pointer;padding:.6rem 1rem}
button.primary{background:var(--beam);color:#111;border-color:var(--beam);font-weight:600}
button:disabled{opacity:.6;cursor:default}
#out{margin-top:1.25rem;padding:.75rem 1rem;border-left:.25rem solid var(--line);background:var(--surface);border-radius:.25rem;min-height:3rem}
#out.ok{border-left-color:#5fd38d}#out.err{border-left-color:#ff7a6b}
small{color:var(--muted)}
</style></head>
<body><main>
<h1>Backend de ToolLighthouse</h1>
<p>Pega la URL del backend. Puede ser solo el host (se añade /detect) o la URL completa del endpoint.</p>
<label>URL del backend
<input id="url" type="url" inputmode="url" autocomplete="off" spellcheck="false" placeholder="http://127.0.0.1:8000">
</label>
<div class="row">
<button id="save" class="primary" type="button">Guardar</button>
<button id="test" type="button">Guardar y probar (POST)</button>
</div>
<div id="out" role="status" aria-live="polite"><small>Sin probar todavía.</small></div>
<p><small>Se guarda en este navegador. La página principal usa esta URL al capturar. La prueba envía una imagen gris de 64×64 px.</small></p>
</main>
<script>
(function () {
  var KEY = "toollighthouse:backend-url";
  var url = document.getElementById("url");
  var out = document.getElementById("out");
  var save = document.getElementById("save");
  var test = document.getElementById("test");

  try { url.value = localStorage.getItem(KEY) || ""; } catch (e) {}

  function show(text, kind) {
    out.className = kind || "";
    out.textContent = text;
  }
  function store() {
    var value = url.value.trim();
    try { localStorage.setItem(KEY, value); return true; }
    catch (e) { show("Este navegador no permite guardar la URL.", "err"); return false; }
  }
  function makeJpeg() {
    return new Promise(function (resolve, reject) {
      var c = document.createElement("canvas");
      c.width = 64; c.height = 64;
      var g = c.getContext("2d");
      g.fillStyle = "#808080"; g.fillRect(0, 0, 64, 64);
      c.toBlob(function (b) { b ? resolve(b) : reject(new Error("jpeg")); }, "image/jpeg", 0.8);
    });
  }

  save.addEventListener("click", function () {
    if (store()) show(url.value.trim() ? "URL guardada." : "URL borrada: se usará BACKEND_URL del servidor.", "ok");
  });

  test.addEventListener("click", async function () {
    if (!store()) return;
    if (!url.value.trim()) { show("Escribe una URL para probar.", "err"); return; }
    test.disabled = true; save.disabled = true;
    show("Enviando imagen de prueba…");
    try {
      var body = new FormData();
      body.append("image", await makeJpeg(), "test.jpg");
      body.append("backend_url", url.value.trim());
      var res = await fetch("/api/capture", { method: "POST", body: body });
      var data = await res.json().catch(function () { return null; });
      if (res.ok) {
        var det = data && data.class ? "Detectó: " + data.class : "Sin detecciones en la imagen de prueba";
        show("Conexión correcta (" + res.status + "). " + det + ".", "ok");
      } else {
        var msg = data && data.message ? data.message : "Error del servidor.";
        show("Falló (" + res.status + "). " + msg, "err");
      }
    } catch (e) {
      show("No se pudo contactar con el servidor de Next.js.", "err");
    } finally {
      test.disabled = false; save.disabled = false;
    }
  });
})();
</script></body></html>`;

export async function GET() {
  if (!isClientBackendUrlEnabled()) {
    return fail(405, "method-not-allowed", "Método no permitido.");
  }
  return new Response(CONFIG_PAGE, {
    headers: { "Content-Type": "text/html; charset=utf-8", "Cache-Control": "no-store" },
  });
}

export async function POST(request: Request) {
  let config: ReturnType<typeof getServerConfig>;
  try {
    config = getServerConfig();
  } catch (e) {
    console.error("[capture] configuración inválida:", e instanceof Error ? e.message : "desconocido");
    return fail(500, "server-config", "El servidor no está configurado correctamente.");
  }

  // 1. Validar la petición entrante.
  const contentType = request.headers.get("content-type")?.toLowerCase() ?? "";
  if (!contentType.startsWith("multipart/form-data")) {
    return fail(415, "unsupported-media-type", "Se esperaba un formulario con la imagen.");
  }

  const declared = Number(request.headers.get("content-length"));
  if (Number.isFinite(declared) && declared > config.maxUploadBytes + MULTIPART_OVERHEAD_BYTES) {
    return fail(413, "too-large", "La imagen es demasiado grande.");
  }

  let form: FormData;
  try {
    form = await request.formData();
  } catch {
    return fail(400, "invalid-body", "No se pudo leer la imagen enviada.");
  }

  const file = form.get(FIELD);
  if (!(file instanceof File) || file.size === 0) {
    return fail(400, "invalid-image", "Falta la imagen o está vacía.");
  }
  if (file.size > config.maxUploadBytes) {
    return fail(413, "too-large", "La imagen es demasiado grande.");
  }
  if (file.type !== "image/jpeg") {
    return fail(400, "invalid-image", "La imagen debe ser JPEG.");
  }
  const buffer = await file.arrayBuffer();
  if (!isJpeg(buffer)) {
    return fail(400, "invalid-image", "El archivo no es una imagen JPEG válida.");
  }

  // 2. Elegir destino. Por defecto sale solo del entorno del servidor; el navegador
  //    puede sustituirlo únicamente si ALLOW_CLIENT_BACKEND_URL=true.
  let target: string;
  const override = form.get(BACKEND_URL_FIELD);
  if (config.allowClientBackendUrl && typeof override === "string" && override.trim() !== "") {
    const parsed = parseClientBackendUrl(override, config.detectPath);
    if (!parsed) {
      return fail(400, "invalid-backend-url", "La URL del backend no es válida. Usa http:// o https://.");
    }
    target = parsed;
  } else if (config.backendUrl) {
    target = `${config.backendUrl}${config.detectPath}`;
  } else {
    return fail(400, "missing-backend-url", "Falta la URL del backend. Configúrala en /api/capture.");
  }

  // 3. Reenviar al backend.
  const upstreamBody = new FormData();
  upstreamBody.append(FIELD, new Blob([buffer], { type: "image/jpeg" }), "frame.jpg");

  const signal = AbortSignal.any([request.signal, AbortSignal.timeout(config.backendTimeoutMs)]);

  let upstream: Response;
  let payload: unknown;
  try {
    upstream = await fetch(target, {
      method: "POST",
      body: upstreamBody,
      signal,
      cache: "no-store",
      redirect: "error", // no seguir redirecciones hacia otros destinos
    });

    if (!upstream.ok) {
      await upstream.body?.cancel().catch(() => undefined);
      console.error(`[capture] el backend respondió ${upstream.status}`);
      return fail(
        upstreamErrorStatus(upstream.status),
        "upstream-error",
        "El servicio de detección no pudo procesar la imagen.",
      );
    }

    try {
      payload = await upstream.json();
    } catch (e) {
      if (signal.aborted) throw e; // timeout o cancelación durante la lectura
      payload = undefined;
    }
  } catch (e) {
    if (request.signal.aborted) {
      return fail(499, "aborted", "La petición se canceló.");
    }
    if (signal.aborted) {
      console.error("[capture] timeout esperando al backend");
      return fail(504, "upstream-timeout", "El servicio de detección tardó demasiado en responder.");
    }
    // Se registra solo el tipo de error: el mensaje podría contener la URL privada.
    console.error("[capture] fallo de conexión con el backend:", e instanceof Error ? e.name : "desconocido");
    return fail(502, "upstream-unreachable", "El servicio de detección no está disponible.");
  }

  // 4. Validar y devolver únicamente el contrato esperado.
  const result = parseDetectionResult(payload);
  if (!result) {
    console.error("[capture] el backend devolvió una respuesta con formato inesperado");
    return fail(502, "upstream-invalid", "El servicio de detección devolvió una respuesta inválida.");
  }
  return NextResponse.json(result, { headers: { "Cache-Control": "no-store" } });
}