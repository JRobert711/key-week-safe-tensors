import { VOICE_CLASSES, type VoiceClassName } from "@/lib/voiceContract";
import type { ModelKind } from "@/types/detection";

export type { VoiceClassName };

export const VOICE_COOLDOWN_MS = 8_000;

export interface TransitionState {
  previousKey: string | null;
  lastAnnouncedAt: number;
}

export const INITIAL_TRANSITION_STATE: TransitionState = {
  previousKey: null,
  lastAnnouncedAt: Number.NEGATIVE_INFINITY,
};

export interface TransitionInput {
  classNames: readonly string[];
  model: ModelKind | null;
  now: number;
}

export interface TransitionResult {
  state: TransitionState;
  announce: VoiceClassName[] | null;
}

/** Normaliza el conjunto a las clases admitidas, únicas y con orden estable. */
export function announceableClasses(
  classNames: readonly string[],
  model: ModelKind | null,
): VoiceClassName[] {
  if (model !== "custom") return [];
  const found = new Set(classNames);
  return VOICE_CLASSES.filter((name) => found.has(name));
}

export function nextTransition(
  previous: TransitionState,
  input: TransitionInput,
): TransitionResult {
  const classNames = announceableClasses(input.classNames, input.model);
  const key = classNames.join(",");

  if (!key) {
    return {
      state: { ...previous, previousKey: null },
      announce: null,
    };
  }

  if (key === previous.previousKey) {
    return { state: previous, announce: null };
  }

  const elapsed = input.now - previous.lastAnnouncedAt;
  const canAnnounce = elapsed >= VOICE_COOLDOWN_MS;
  if (!canAnnounce) {
    // No registrar este conjunto como anunciado: si sigue presente al terminar
    // el cooldown, podrá anunciarse sin necesitar otro cambio de detección.
    return { state: previous, announce: null };
  }

  return {
    state: { previousKey: key, lastAnnouncedAt: input.now },
    announce: classNames,
  };
}
