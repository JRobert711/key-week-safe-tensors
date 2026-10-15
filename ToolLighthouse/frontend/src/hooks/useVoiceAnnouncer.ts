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
import type { DetectionBox, ModelKind } from "@/types/detection";
import type { LowConfidenceVoiceEvent, VoiceEvent, VoicePlaybackStatus } from "@/types/voice";

/** Un audio que llega más tarde que esto ya no corresponde a la transición que lo pidió. */
export const STALE_AFTER_MS = 8_000;
const RETRY_AFTER_DEFAULT_MS = 3_200;
const MAX_TRANSITION_ID = 2_147_483_647;

function makeId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID().replace(/-/g, "_");
  }
  return `v_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 12)}`;
}

export interface VoiceAnnouncerInput {
  /** Cajas de detección del fotograma actual; el anuncio usa el conjunto de clases únicas. */
  boxes: DetectionBox[] | null;
  /** El modelo fallback nunca se anuncia. */
  model: ModelKind | null;
  phase: LivePhase;
  running: boolean;
  enabled?: boolean;
  /**
   * Aviso "Creo haber visto X" visible ahora (solo su id). Se habla una vez por aviso, respetando `enabled`.
   * Si ya suena o se está pidiendo otro audio, el aviso queda solo en texto (el banner siempre lo muestra).
   */
  hintId?: string | null;
}

type AudioKind = "detection" | "hint";

export interface VoiceAnnouncerState {
  status: VoicePlaybackStatus;
  error: string | null;
  needsGesture: boolean;
  /** Clases del último conjunto anunciado. */
  lastAnnounced: VoiceClassName[] | null;
}

const INITIAL: VoiceAnnouncerState = {
  status: "idle",
  error: null,
  needsGesture: false,
  lastAnnounced: null,
};

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

export function useVoiceAnnouncer({ boxes, model, phase, running, enabled = true, hintId = null }: VoiceAnnouncerInput) {
  const [snapshot, setSnapshot] = useState<VoiceAnnouncerState>(INITIAL);
  const transitionRef = useRef<TransitionState>(INITIAL_TRANSITION_STATE);
  const sessionIdRef = useRef<string | null>(null);
  const transitionIdRef = useRef(0);
  const seqRef = useRef(0);
  const abortRef = useRef<AbortController | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef<string | null>(null);
  const pendingRef = useRef<Blob | null>(null);
  const blockedRef = useRef<Blob | null>(null);
  const mountedRef = useRef(true);
  const enabledRef = useRef(enabled);
  /** Tipo de la petición en curso y del audio que suena: permiten cortar solo la voz de un aviso. */
  const requestKindRef = useRef<AudioKind | null>(null);
  const audioKindRef = useRef<AudioKind | null>(null);
  const spokenHintRef = useRef<string | null>(null);

  const releaseAudio = useCallback(() => {
    const audio = audioRef.current;
    if (audio) {
      audio.onended = null;
      audio.onerror = null;
      audio.pause();
      audio.removeAttribute("src");
      audioRef.current = null;
    }
    audioKindRef.current = null;
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current);
      urlRef.current = null;
    }
  }, []);

  const playBlob = useCallback(async (blob: Blob, kind: AudioKind = "detection") => {
    if (!enabledRef.current || !mountedRef.current) return;
    releaseAudio();
    audioKindRef.current = kind;
    const url = URL.createObjectURL(blob);
    urlRef.current = url;
    const audio = new Audio(url);
    audioRef.current = audio;

    audio.onended = () => {
      if (audioRef.current !== audio) return;
      releaseAudio();
      const next = pendingRef.current;
      pendingRef.current = null;
      if (next && enabledRef.current) {
        void playBlob(next);
      } else if (mountedRef.current) {
        setSnapshot((prev) => ({ ...prev, status: abortRef.current ? "loading" : "idle" }));
      }
    };

    audio.onerror = () => {
      if (audioRef.current !== audio) return;
      releaseAudio();
      pendingRef.current = null;
      if (mountedRef.current) setSnapshot((prev) => ({ ...prev, status: "error", error: "El navegador no pudo reproducir el audio." }));
    };

    try {
      await audio.play();
      if (audioRef.current === audio && mountedRef.current && enabledRef.current) {
        setSnapshot((prev) => ({ ...prev, status: "playing", error: null, needsGesture: false }));
      }
    } catch (e) {
      if (audioRef.current !== audio) return;
      releaseAudio();
      if (!mountedRef.current || !enabledRef.current) return;
      if (kind === "hint") {
        // Un aviso es efímero: si el navegador bloquea el audio no se reintenta más tarde (el banner ya lo muestra).
        setSnapshot((prev) => ({ ...prev, status: "idle", error: null }));
      } else if (e instanceof DOMException && e.name === "NotAllowedError") {
        blockedRef.current = blob;
        setSnapshot((prev) => ({ ...prev, status: "idle", error: null, needsGesture: true }));
      } else {
        setSnapshot((prev) => ({ ...prev, status: "error", error: "No se pudo iniciar la reproducción de voz." }));
      }
    }
  }, [releaseAudio]);

  const cancelAll = useCallback(() => {
    seqRef.current += 1;
    abortRef.current?.abort();
    abortRef.current = null;
    requestKindRef.current = null;
    pendingRef.current = null;
    blockedRef.current = null;
    releaseAudio();
  }, [releaseAudio]);

  const endSession = useCallback(() => {
    cancelAll();
    transitionRef.current = INITIAL_TRANSITION_STATE;
    sessionIdRef.current = null;
    transitionIdRef.current = 0;
    spokenHintRef.current = null;
    if (mountedRef.current) setSnapshot(INITIAL);
  }, [cancelAll]);

  const announce = useCallback(async (classNames: VoiceClassName[]) => {
    if (!enabledRef.current || !mountedRef.current || classNames.length === 0) return;
    const seq = ++seqRef.current;
    abortRef.current?.abort();
    const controller = new AbortController();
    abortRef.current = controller;
    requestKindRef.current = "detection";
    if (!sessionIdRef.current) sessionIdRef.current = makeId();

    const event: VoiceEvent = {
      event_id: makeId(),
      session_id: sessionIdRef.current,
      class_names: classNames,
      transition_id: transitionIdRef.current,
    };
    transitionIdRef.current = transitionIdRef.current >= MAX_TRANSITION_ID ? 0 : transitionIdRef.current + 1;
    const startedAt = Date.now();
    setSnapshot((prev) => ({
      ...prev,
      status: audioRef.current ? prev.status : "loading",
      error: null,
      lastAnnounced: classNames,
    }));

    try {
      let blob: Blob | null = null;
      for (let attempt = 0; attempt < 2 && blob === null; attempt++) {
        try {
          blob = await requestVoiceAudio(event, { signal: controller.signal });
        } catch (e) {
          const retriable = e instanceof VoiceError && e.status === 429 && attempt === 0;
          if (!retriable) throw e;
          const delay = (e.retryAfterSeconds ?? 0) * 1000 || RETRY_AFTER_DEFAULT_MS;
          if (!(await wait(delay, controller.signal))) return;
          if (seq !== seqRef.current || !enabledRef.current) return;
        }
      }
      if (blob === null || seq !== seqRef.current || !mountedRef.current || !enabledRef.current) return;
      if (Date.now() - startedAt > STALE_AFTER_MS) {
        setSnapshot((prev) => ({ ...prev, status: audioRef.current ? prev.status : "idle" }));
        return;
      }
      if (audioRef.current) pendingRef.current = blob;
      else await playBlob(blob);
    } catch (e) {
      if (seq !== seqRef.current) return;
      if (e instanceof VoiceError && e.code === "aborted") return;
      if (!mountedRef.current || !enabledRef.current) return;
      const message = e instanceof VoiceError ? e.userMessage : "No se pudo generar el anuncio de voz.";
      setSnapshot((prev) => ({ ...prev, status: audioRef.current ? prev.status : "error", error: message }));
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
        requestKindRef.current = null;
      }
    }
  }, [playBlob]);

  /** Habla un aviso de baja confianza. Nunca interrumpe ni reemplaza otro audio o petición en curso. */
  const announceHint = useCallback(async (id: string) => {
    if (!enabledRef.current || !mountedRef.current) return;
    if (abortRef.current || audioRef.current) return; // ocupado: el aviso queda solo en texto
    const seq = ++seqRef.current;
    const controller = new AbortController();
    abortRef.current = controller;
    requestKindRef.current = "hint";

    const event: LowConfidenceVoiceEvent = { kind: "low_confidence", event_id: makeId(), hint_id: id };
    const startedAt = Date.now();
    setSnapshot((prev) => ({ ...prev, status: "loading", error: null }));

    try {
      const blob = await requestVoiceAudio(event, { signal: controller.signal });
      if (seq !== seqRef.current || !mountedRef.current || !enabledRef.current) return;
      if (Date.now() - startedAt > STALE_AFTER_MS || audioRef.current) {
        setSnapshot((prev) => ({ ...prev, status: audioRef.current ? prev.status : "idle" }));
        return;
      }
      await playBlob(blob, "hint");
    } catch (e) {
      if (seq !== seqRef.current) return;
      if (!mountedRef.current || !enabledRef.current) return;
      if (e instanceof VoiceError && e.code === "aborted") return;
      // 404/409/429: el aviso ya caducó, se cerró o la voz está ocupada. No es un error para la persona.
      const quiet = e instanceof VoiceError && (e.status === 404 || e.status === 409 || e.status === 429);
      const message = e instanceof VoiceError ? e.userMessage : "No se pudo generar el anuncio de voz.";
      setSnapshot((prev) => ({
        ...prev,
        status: audioRef.current ? prev.status : quiet ? "idle" : "error",
        error: quiet ? null : message,
      }));
    } finally {
      if (abortRef.current === controller) {
        abortRef.current = null;
        requestKindRef.current = null;
      }
    }
  }, [playBlob]);

  /** Corta solo la voz de un aviso (petición o audio); la de detecciones no se toca. */
  const cancelHint = useCallback(() => {
    if (requestKindRef.current === "hint") {
      seqRef.current += 1;
      abortRef.current?.abort();
      abortRef.current = null;
      requestKindRef.current = null;
      if (mountedRef.current) setSnapshot((prev) => ({ ...prev, status: audioRef.current ? prev.status : "idle" }));
    }
    if (audioKindRef.current === "hint") {
      releaseAudio();
      const next = pendingRef.current;
      pendingRef.current = null;
      if (next && enabledRef.current) void playBlob(next);
      else if (mountedRef.current) setSnapshot((prev) => ({ ...prev, status: "idle" }));
    }
  }, [playBlob, releaseAudio]);

  useEffect(() => {
    enabledRef.current = enabled;
    if (!enabled) {
      cancelAll();
      setSnapshot((prev) => ({ ...prev, status: "idle", error: null, needsGesture: false }));
    }
  }, [enabled, cancelAll]);

  useEffect(() => {
    if (!running) endSession();
  }, [running, endSession]);

  useEffect(() => {
    if (!running || phase !== "running") return;
    try {
      const classes = (boxes ?? []).map((box) => box.class);
      const result = nextTransition(transitionRef.current, { classNames: classes, model, now: Date.now() });
      transitionRef.current = result.state;
      if (enabled && result.announce) void announce(result.announce);
    } catch {
      // Un fallo de voz jamás debe afectar a la detección.
    }
  }, [boxes, model, phase, running, enabled, announce]);

  useEffect(() => {
    if (!hintId) {
      // El aviso se fue: si su audio aún se está pidiendo, se cancela; el que ya suena termina la frase.
      if (requestKindRef.current === "hint") cancelHint();
      return;
    }
    if (!enabled || !running || phase !== "running" || model === "fallback") return;
    if (spokenHintRef.current === hintId) return;
    spokenHintRef.current = hintId;
    void announceHint(hintId);
  }, [hintId, enabled, running, phase, model, announceHint, cancelHint]);

  const retryPlayback = useCallback(() => {
    const blob = blockedRef.current;
    blockedRef.current = null;
    if (blob && enabledRef.current) void playBlob(blob);
  }, [playBlob]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      cancelAll();
    };
  }, [cancelAll]);

  return { ...snapshot, retryPlayback, cancel: endSession, cancelHint };
}
