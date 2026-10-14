/** Evento cerrado que solicita una frase de voz para una clase detectada. */
export interface VoiceEvent {
  event_id: string;
  session_id: string;
  class_name: string;
  transition_id: number;
}

/** Estados visibles del flujo de prueba y reproducción de voz. */
export type VoicePlaybackStatus = "idle" | "loading" | "playing" | "error";
