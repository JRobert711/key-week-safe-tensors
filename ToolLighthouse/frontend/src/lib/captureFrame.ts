/** Captura de un fotograma del <video> como JPEG. Solo se ejecuta en el navegador. */

export type CaptureErrorCode = "video-not-ready" | "encode-failed" | "too-large";

export class CaptureError extends Error {
  constructor(
    public readonly code: CaptureErrorCode,
    public readonly userMessage: string,
  ) {
    super(userMessage);
    this.name = "CaptureError";
  }
}

export interface CaptureOptions {
  /** Ancho máximo en píxeles; no se amplía una imagen más pequeña. */
  maxWidth?: number;
  /** Calidad JPEG inicial (0 a 1). Si el resultado supera maxBytes se reintenta con menos calidad. */
  quality?: number;
  maxBytes?: number;
}

const FALLBACK_QUALITIES = [0.7, 0.55];

function toJpegBlob(canvas: HTMLCanvasElement, quality: number): Promise<Blob | null> {
  return new Promise((resolve) => canvas.toBlob(resolve, "image/jpeg", quality));
}

export async function captureFrame(
  video: HTMLVideoElement,
  { maxWidth = 640, quality = 0.85, maxBytes = 1_048_576 }: CaptureOptions = {},
): Promise<Blob> {
  // readyState 2 (HAVE_CURRENT_DATA) o más: hay al menos un fotograma disponible.
  if (video.readyState < 2 || video.videoWidth === 0 || video.videoHeight === 0) {
    throw new CaptureError(
      "video-not-ready",
      "La cámara todavía no está lista. Espera a que aparezca la imagen e inténtalo de nuevo.",
    );
  }

  const scale = Math.min(1, maxWidth / video.videoWidth);
  const canvas = document.createElement("canvas");
  canvas.width = Math.round(video.videoWidth * scale);
  canvas.height = Math.round(video.videoHeight * scale);

  const context = canvas.getContext("2d");
  if (!context) {
    throw new CaptureError("encode-failed", "No se pudo preparar la imagen para enviarla.");
  }
  context.drawImage(video, 0, 0, canvas.width, canvas.height);

  for (const q of [quality, ...FALLBACK_QUALITIES.filter((value) => value < quality)]) {
    const blob = await toJpegBlob(canvas, q);
    // Algunos navegadores devuelven otro formato si no pueden codificar JPEG.
    if (!blob || blob.type !== "image/jpeg") {
      throw new CaptureError("encode-failed", "No se pudo convertir el fotograma a JPEG.");
    }
    if (blob.size <= maxBytes) return blob;
  }

  throw new CaptureError(
    "too-large",
    "La imagen capturada es demasiado grande para enviarla. Prueba con una resolución menor.",
  );
}
