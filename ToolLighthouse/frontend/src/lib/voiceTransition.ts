import { VOICE_CLASSES } from "@/lib/voiceContract";
import type { ModelKind } from "@/types/detection";

/**
 * Política de anuncios por transición de la clase OFICIAL (la que ya pasó el filtro de estabilidad
 * de 7 de 10 fotogramas). Funciones puras: sin React, sin DOM, sin reloj propio.
 *
 *   null -> cautin             : anunciar cautín
 *   cautin -> cautin           : no anunciar (clase persistente)
 *   cautin -> fuente_voltaje   : anunciar fuente de voltaje
 *   cautin -> null             : silencio
 *   null -> null               : nada
 *   clase fuera del enum, o modelo "fallback" (nombres COCO) : se trata como null; nunca se anuncia
 */

export type VoiceClassName = (typeof VOICE_CLASSES)[number];

/**
 * Misma clase, mismo anuncio: tras anunciar una clase no se repite durante este tiempo aunque el
 * filtro de estabilidad parpadee (X -> null -> X). Poner 0 para anunciar siempre en null -> X.
 */
export const SAME_CLASS_COOLDOWN_MS = 8_000;

export interface TransitionState {
  /** Última clase "anunciable" vista (null si no había, o si no era anunciable). */
  previous: VoiceClassName | null;
  lastAnnounced: { className: VoiceClassName; at: number } | null;
}

export const INITIAL_TRANSITION_STATE: TransitionState = { previous: null, lastAnnounced: null };

export interface TransitionInput {
  official: string | null;
  model: ModelKind | null;
  /** Instante actual en ms (lo aporta quien llama, para que la función siga siendo pura). */
  now: number;
  cooldownMs?: number;
}

export interface TransitionResult {
  state: TransitionState;
  /** Clase a anunciar ahora, o null si no hay que decir nada. */
  announce: VoiceClassName | null;
}

/** La clase oficial solo es anunciable si está en el enum cerrado y el modelo no es el de respaldo. */
export function announceableClass(official: string | null, model: ModelKind | null): VoiceClassName | null {
  if (official === null || model === "fallback") return null;
  return (VOICE_CLASSES as readonly string[]).includes(official) ? (official as VoiceClassName) : null;
}

export function nextTransition(state: TransitionState, input: TransitionInput): TransitionResult {
  const current = announceableClass(input.official, input.model);
  if (current === state.previous) return { state, announce: null };

  if (current === null) return { state: { ...state, previous: null }, announce: null };

  const cooldown = input.cooldownMs ?? SAME_CLASS_COOLDOWN_MS;
  const last = state.lastAnnounced;
  if (last !== null && last.className === current && input.now - last.at < cooldown) {
    return { state: { ...state, previous: current }, announce: null };
  }
  return { state: { previous: current, lastAnnounced: { className: current, at: input.now } }, announce: current };
}
