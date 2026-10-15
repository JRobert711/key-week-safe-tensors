/** Evento cerrado que solicita una frase de voz para un conjunto de clases detectadas. */
export interface VoiceEvent {
  event_id: string;
  session_id: string;
  class_names: string[];
  transition_id: number;
}

/**
 * Pide hablar un aviso de baja confianza. Solo identifica el aviso: la frase la toma el backend de su
 * registro (tabla cerrada), así que lo que suena es exactamente lo que muestra el banner.
 */
export interface LowConfidenceVoiceEvent {
  kind: "low_confidence";
  event_id: string;
  hint_id: string;
}

export type VoiceRequest = VoiceEvent | LowConfidenceVoiceEvent;

/** Estados visibles del flujo de prueba y reproducción de voz. */
export type VoicePlaybackStatus = "idle" | "loading" | "playing" | "error";
