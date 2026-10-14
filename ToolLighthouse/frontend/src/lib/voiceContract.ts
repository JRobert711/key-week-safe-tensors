import type { VoiceEvent } from "@/types/voice";

const ID_PATTERN = /^[A-Za-z0-9_-]{8,64}$/;
const MAX_CLASS_NAME_LENGTH = 64;
const MAX_TRANSITION_ID = 2_147_483_647;

/** Clases que el backend de voz admite (sin texto libre). */
export const VOICE_CLASSES = [
  "cautin",
  "multimetro_digital",
  "multimetro_analogico",
  "estacion_soldadura",
  "caja_sensores",
  "ventilador_pasco",
  "bascula",
  "capacitor_variable",
  "fuente_voltaje",
] as const;

/** Valida y reconstruye el contrato sin conservar campos desconocidos. */
export function parseVoiceEvent(raw: unknown): VoiceEvent | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const value = raw as Record<string, unknown>;
  const keys = Object.keys(value).sort();
  const expected = ["class_name", "event_id", "session_id", "transition_id"];
  if (keys.length !== expected.length || !keys.every((key, index) => key === expected[index])) return null;

  const { event_id, session_id, class_name, transition_id } = value;
  if (typeof event_id !== "string" || !ID_PATTERN.test(event_id)) return null;
  if (typeof session_id !== "string" || !ID_PATTERN.test(session_id)) return null;
  if (typeof class_name !== "string" || class_name.length < 1 || class_name.length > MAX_CLASS_NAME_LENGTH) return null;
  if (!(VOICE_CLASSES as readonly string[]).includes(class_name)) return null;
  if (typeof transition_id !== "number" || !Number.isInteger(transition_id) || transition_id < 0 || transition_id > MAX_TRANSITION_ID) return null;

  return { event_id, session_id, class_name, transition_id };
}

/** Comprueba que una respuesta binaria realmente declare un tipo de audio. */
export function isAudioContentType(value: string | null): boolean {
  return typeof value === "string" && /^audio\/[a-z0-9.+-]+(?:\s*;.*)?$/i.test(value.trim());
}
