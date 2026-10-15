import { VOICE_CLASSES } from "@/lib/voiceContract";
import type { HintServerMessage } from "@/types/hints";

const ID_PATTERN = /^[A-Za-z0-9_-]{8,64}$/;
const MAX_FRAME_CHARS = 4096;
const MAX_MESSAGE_CHARS = 300;
const PROTOCOL_VERSION = 1;

/** Valida un mensaje del WebSocket (contrato v1). Devuelve null si no es válido: nunca lanza. */
export function parseHintMessage(data: unknown): HintServerMessage | null {
  if (typeof data !== "string" || data.length === 0 || data.length > MAX_FRAME_CHARS) return null;
  let raw: unknown;
  try {
    raw = JSON.parse(data);
  } catch {
    return null;
  }
  if (typeof raw !== "object" || raw === null || Array.isArray(raw)) return null;
  const value = raw as Record<string, unknown>;
  if (value.v !== PROTOCOL_VERSION) return null;

  switch (value.type) {
    case "hello":
      return { type: "hello" };
    case "hint_cleared":
      return typeof value.hint_id === "string" && ID_PATTERN.test(value.hint_id)
        ? { type: "hint_cleared", hint_id: value.hint_id }
        : null;
    case "low_confidence": {
      const { hint_id, confidence, message, ts } = value;
      const className = value.class;
      if (typeof hint_id !== "string" || !ID_PATTERN.test(hint_id)) return null;
      if (typeof className !== "string" || !(VOICE_CLASSES as readonly string[]).includes(className)) return null;
      if (typeof confidence !== "number" || !Number.isFinite(confidence) || confidence < 0 || confidence > 1) return null;
      if (typeof message !== "string" || message.trim() === "" || message.length > MAX_MESSAGE_CHARS) return null;
      if (typeof ts !== "number" || !Number.isInteger(ts) || ts < 0) return null;
      return { type: "low_confidence", hint_id, class: className, confidence, message, ts };
    }
    default:
      return null;
  }
}
