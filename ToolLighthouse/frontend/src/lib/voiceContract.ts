import type { LowConfidenceVoiceEvent, VoiceEvent, VoiceRequest } from "@/types/voice";

const ID_PATTERN = /^[A-Za-z0-9_-]{8,64}$/;
const MAX_TRANSITION_ID = 2_147_483_647;

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

export type VoiceClassName = (typeof VOICE_CLASSES)[number];

export function isAudioContentType(contentType: string | null): boolean {
  return typeof contentType === "string" && /^audio\/[a-z0-9.+-]+(?:\s*;.*)?$/i.test(contentType.trim());
}

export function parseVoiceEvent(raw: unknown): VoiceEvent | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const value = raw as Record<string, unknown>;
  const keys = Object.keys(value).sort();
  const expected = ["class_names", "event_id", "session_id", "transition_id"];
  if (keys.length !== expected.length || !keys.every((key, index) => key === expected[index])) return null;

  const { event_id, session_id, class_names, transition_id } = value;
  if (typeof event_id !== "string" || !ID_PATTERN.test(event_id)) return null;
  if (typeof session_id !== "string" || !ID_PATTERN.test(session_id)) return null;
  if (
    !Array.isArray(class_names) ||
    class_names.length < 1 ||
    class_names.length > VOICE_CLASSES.length ||
    !class_names.every(
      (name): name is VoiceClassName =>
        typeof name === "string" && (VOICE_CLASSES as readonly string[]).includes(name),
    ) ||
    new Set(class_names).size !== class_names.length
  ) return null;
  if (
    typeof transition_id !== "number" ||
    !Number.isInteger(transition_id) ||
    transition_id < 0 ||
    transition_id > MAX_TRANSITION_ID
  ) return null;

  return { event_id, session_id, class_names, transition_id };
}

export function parseLowConfidenceVoiceEvent(raw: unknown): LowConfidenceVoiceEvent | null {
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const value = raw as Record<string, unknown>;
  const keys = Object.keys(value).sort();
  if (keys.length !== 3 || keys[0] !== "event_id" || keys[1] !== "hint_id" || keys[2] !== "kind") return null;
  if (value.kind !== "low_confidence") return null;
  const { event_id, hint_id } = value;
  if (typeof event_id !== "string" || !ID_PATTERN.test(event_id)) return null;
  if (typeof hint_id !== "string" || !ID_PATTERN.test(hint_id)) return null;
  return { kind: "low_confidence", event_id, hint_id };
}

/** Acepta un evento de detección (sin "kind") o un aviso de baja confianza; null si no cumple el contrato. */
export function parseVoiceRequest(raw: unknown): VoiceRequest | null {
  if (typeof raw === "object" && raw !== null && !Array.isArray(raw) && "kind" in raw) {
    return parseLowConfidenceVoiceEvent(raw);
  }
  return parseVoiceEvent(raw);
}
