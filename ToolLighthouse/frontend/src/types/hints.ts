/** Aviso de baja confianza ("Creo haber visto ...") recibido por el WebSocket del backend. */
export interface LowConfidenceHint {
  hint_id: string;
  class: string;
  confidence: number;
  /** Frase cerrada construida por el backend; se muestra como texto plano. */
  message: string;
  /** Epoch en segundos. */
  ts: number;
}

/** Mensajes servidor -> cliente del contrato v1. */
export type HintServerMessage =
  | { type: "hello" }
  | ({ type: "low_confidence" } & LowConfidenceHint)
  | { type: "hint_cleared"; hint_id: string };

export type HintsStatus = "idle" | "connecting" | "open" | "reconnecting" | "disabled";

/** Estado del reporte "No es eso" (falso positivo). "unavailable": el backend no lo ofrece, se oculta el botón. */
export type HintFeedbackStatus = "idle" | "sending" | "sent" | "error" | "unavailable";
