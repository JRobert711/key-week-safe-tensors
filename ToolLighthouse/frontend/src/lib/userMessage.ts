import { SendError } from "@/lib/captureApi";
import { CaptureError } from "@/lib/captureFrame";

/** Mensaje apto para el usuario a partir de cualquier error del flujo de captura y envío. */
export function userMessageOf(error: unknown): string {
  return error instanceof CaptureError || error instanceof SendError
    ? error.userMessage
    : "Ocurrió un error inesperado. Vuelve a intentarlo.";
}
