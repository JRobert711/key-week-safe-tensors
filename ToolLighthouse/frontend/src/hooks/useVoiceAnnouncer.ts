"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import type { LivePhase } from "@/hooks/useLiveDetection";
import { requestVoiceAudio, VoiceError } from "@/lib/voiceApi";
import {
  INITIAL_TRANSITION_STATE,
  nextTransition,
  type TransitionState,
  type VoiceClassName,
} from "@/lib/voiceTransition";
import type { ModelKind } from "@/types/detection";
import type { VoiceEvent, VoicePlaybackStatus } from "@/types/voice";

/**
 * Anuncios automáticos por transición estable (Fase 4). Se alimenta con lo que ya expone
 * useLiveDetection (official, model, phase, running) y NO modifica el bucle de detección:
 * la síntesis corre en un flujo independiente y sus errores nunca llegan a la detección.
 *
 *  - Una petición por TRANSICIÓN (nunca por fotograma, nunca para una clase persistente).
 *  - Una sola solicitud en curso: una transición nueva cancela la anterior (gana la más reciente).
 *  - Si una frase ya suena, se deja terminar; solo se guarda UN audio pendiente (el más reciente).
 *  - Audio que tarda más de STALE_AFTER_MS en llegar se descarta (ya no describe lo que se ve).
 *  - Al terminar la sesión en vivo (cámara/modo en vivo detenidos) se cancela todo y se reinicia
 *    session_id y transition_id; las respuestas de la sesión vieja se ignoran.
 *  - Cada URL temporal se revoca en ended/error/cancelación/desmontaje.
 */

/** Un audio que llega más tarde que esto ya no corresponde a la transición que lo pidió. */
export const STALE_AFTER_MS = 8_000;
/** Espera por defecto antes de reintentar tras un 429 (el backend limita 1 síntesis nueva / 3 s por sesión). */
const RETRY_AFTER_DEFAULT_MS = 3_200;
const MAX_TRANSITION_ID = 2_147_483_647;

function makeId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID().replace(/-/g, "_");
  }
  return `v_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 12)}`;
}

export interface VoiceAnnouncerInput {
  /** `live.official`: clase oficial ya filtrada por estabilidad. */
  official: string | null;
  /** `live.model`: "fallback" nunca se anuncia. */
  model: ModelKind | null;
  /** `live.phase`: solo se evalúan transiciones con la fase "running". */
  phase: LivePhase;
  /** `live.running`: al pasar a false termina la sesión de voz. */
  running: boolean;
  /** Apaga los anuncios sin tocar la detección (Fase 5 conectará aquí el interruptor). */
  enabled?: boolean;
}

export interface VoiceAnnouncerState {
  status: VoicePlaybackStatus;
  error: string | null;
  /** El navegador bloqueó el autoplay: llamar retryPlayback() desde un clic del usuario. */
  needsGesture: boolean;
  /** Última clase anunciada (informativo). */
  lastAnnounced: VoiceClassName | null;
}

const INITIAL: VoiceAnnouncerState = { status: "idle", error: null, needsGesture: false, lastAnnounced: null };

/** Espera `ms` o hasta que se cancele; devuelve false si se canceló. */
function wait(ms: number, signal: AbortSignal): Promise<boolean> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve(false);
    const done = (ok: boolean) => {
      clearTimeout(timer);
      signal.removeEventListener("abort", onAbort);
      resolve(ok);
    };
    const onAbort = () => done(false);
    const timer = setTimeout(() => done(true), ms);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

export function useVoiceAnnouncer({ official, model, phase, running, enabled = true }: VoiceAnnouncerInput) {
  const [snapshot, setSnapshot] = useState<VoiceAnnouncerState>(INITIAL);

  const transitionRef = useRef<TransitionState>(INITIAL_TRANSITION_STATE);
  const sessionIdRef = useRef<string | null>(null);
  const transitionIdRef = useRef(0);
  const seqRef = useRef(0); // invalida respuestas de solicitudes anteriores y de sesiones terminadas
  const abortRef = useRef<AbortController | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef<string | null>(null);
  const pendingRef = useRef<Blob | null>(null); // listo, esperando a que termine la frase actual
  const blockedRef = useRef<Blob | null>(null); // bloqueado por la política de autoplay
  const mountedRef = useRef(true);
  const enabledRef = useRef(enabled);

  const releaseAudio = useCallback(() => {
    const audio = audioRef.current;
    if (audio) {
      audio.onended = null;
      audio.onerror = null;
      audio.pause();
      audio.removeAttribute("src");
      audioRef.current = null;
    }
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current);
      urlRef.current = null;
    }
  }, []);

  const playBlob = useCallback(
    async (blob: Blob) => {
      releaseAudio();
      const url = URL.createObjectURL(blob);
      urlRef.current = url;
      const audio = new Audio(url);
      audioRef.current = audio;

      audio.onended = () => {
        if (audioRef.current !== audio) return;
        releaseAudio();
        const next = pendingRef.current;
        pendingRef.current = null;
        if (next) {
          void playBlob(next);
        } else if (mountedRef.current) {
          setSnapshot((prev) => ({ ...prev, status: abortRef.current ? "loading" : "idle" }));
        }
      };
      audio.onerror = () => {
        if (audioRef.current !== audio) return;
        releaseAudio();
        if (mountedRef.current) {
          setSnapshot((prev) => ({ ...prev, status: "error", error: "El navegador no pudo reproducir el audio." }));
        }
      };

      try {
        await audio.play();
        if (audioRef.current === audio && mountedRef.current) {
          setSnapshot((prev) => ({ ...prev, status: "playing", error: null, needsGesture: false }));
        }
      } catch (e) {
        if (audioRef.current !== audio) return; // se canceló mientras arrancaba: no es un error
        releaseAudio();
        if (!mountedRef.current) return;
        if (e instanceof DOMException && e.name === "NotAllowedError") {
          blockedRef.current = blob;
          setSnapshot((prev) => ({ ...prev, status: "idle", error: null, needsGesture: true }));
        } else {
          setSnapshot((prev) => ({ ...prev, status: "error", error: "No se pudo iniciar la reproducción de voz." }));
        }
      }
    },
    [releaseAudio],
  );

  /** Cancela solicitudes, descarta audios pendientes y detiene la reproducción. */
  const cancelAll = useCallback(() => {
    seqRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    pendingRef.current = null;
    blockedRef.current = null;
    releaseAudio();
  }, [releaseAudio]);

  /** Fin de la sesión en vivo: nada de lo pendiente sobrevive y los ids se reinician. */
  const endSession = useCallback(() => {
    cancelAll();
    transitionRef.current = INITIAL_TRANSITION_STATE;
    sessionIdRef.current = null;
    transitionIdRef.current = 0;
    if (mountedRef.current) setSnapshot(INITIAL);
  }, [cancelAll]);

  const announce = useCallback(
    async (className: VoiceClassName) => {
      const seq = ++seqRef.current;
      abortRef.current?.abort(); // la solicitud anterior, si aún no sonó, queda obsoleta
      const controller = new AbortController();
      abortRef.current = controller;

      if (!sessionIdRef.current) sessionIdRef.current = makeId();
      const event: VoiceEvent = {
        event_id: makeId(),
        session_id: sessionIdRef.current,
        class_name: className,
        transition_id: transitionIdRef.current,
      };
      transitionIdRef.current = (transitionIdRef.current + 1) % MAX_TRANSITION_ID;
      const startedAt = Date.now();
      setSnapshot((prev) => ({ ...prev, status: audioRef.current ? prev.status : "loading", error: null, lastAnnounced: className }));

      try {
        let blob: Blob | null = null;
        for (let attempt = 0; attempt < 2 && blob === null; attempt++) {
          try {
            blob = await requestVoiceAudio(event, { signal: controller.signal });
          } catch (e) {
            // 429: el backend limita las síntesis nuevas por sesión. Un único reintento, si sigue vigente.
            const retriable = e instanceof VoiceError && e.status === 429 && attempt === 0;
            if (!retriable) throw e;
            const delay = (e.retryAfterSeconds ?? 0) * 1000 || RETRY_AFTER_DEFAULT_MS;
            if (!(await wait(delay, controller.signal))) return;
            if (seq !== seqRef.current) return;
          }
        }
        if (blob === null || seq !== seqRef.current || !mountedRef.current) return; // obsoleta o sesión terminada
        if (Date.now() - startedAt > STALE_AFTER_MS) {
          setSnapshot((prev) => ({ ...prev, status: audioRef.current ? prev.status : "idle" }));
          return; // llegó tarde: ya no describe lo que se ve
        }
        if (audioRef.current) pendingRef.current = blob; // deja terminar la frase actual
        else await playBlob(blob);
      } catch (e) {
        if (seq !== seqRef.current) return;
        if (e instanceof VoiceError && e.code === "aborted") return;
        if (!mountedRef.current) return;
        const message = e instanceof VoiceError ? e.userMessage : "No se pudo generar el anuncio de voz.";
        setSnapshot((prev) => ({ ...prev, status: audioRef.current ? prev.status : "error", error: message }));
      } finally {
        if (abortRef.current === controller) abortRef.current = null;
      }
    },
    [playBlob],
  );

  useEffect(() => {
    enabledRef.current = enabled;
  }, [enabled]);

  // Sesión terminada (cámara o modo en vivo detenidos): cancelar y reiniciar.
  useEffect(() => {
    if (!running) endSession();
  }, [running, endSession]);

  // Detección de transiciones. Se ejecuta por cambio de official/model/phase, no por fotograma.
  useEffect(() => {
    if (!running || !enabled || phase !== "running") return;
    try {
      const result = nextTransition(transitionRef.current, { official, model, now: Date.now() });
      transitionRef.current = result.state;
      if (result.announce) void announce(result.announce);
    } catch {
      /* un fallo de voz jamás debe afectar a la detección */
    }
  }, [official, model, phase, running, enabled, announce]);

  const retryPlayback = useCallback(() => {
    const blob = blockedRef.current;
    blockedRef.current = null;
    if (blob && enabledRef.current) void playBlob(blob);
  }, [playBlob]);

  // Al desmontar: cancelar solicitudes, detener el audio y revocar la URL temporal.
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      cancelAll();
    };
  }, [cancelAll]);

  return { ...snapshot, retryPlayback, cancel: endSession };
}
