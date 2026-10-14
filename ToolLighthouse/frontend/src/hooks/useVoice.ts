"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { requestVoiceAudio, VoiceError } from "@/lib/voiceApi";
import type { VoicePlaybackStatus } from "@/types/voice";

function makeId(): string {
  if (typeof crypto !== "undefined" && typeof crypto.randomUUID === "function") {
    return crypto.randomUUID().replace(/-/g, "_");
  }
  return `v_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 12)}`;
}

interface VoiceSnapshot {
  status: VoicePlaybackStatus;
  message: string | null;
  error: string | null;
  needsGesture: boolean;
}

const INITIAL: VoiceSnapshot = { status: "idle", message: null, error: null, needsGesture: false };

/** Controla la prueba manual de voz, la reproducción, la cancelación y la URL temporal del audio. */
export function useVoice() {
  const [snapshot, setSnapshot] = useState<VoiceSnapshot>(INITIAL);
  const abortRef = useRef<AbortController | null>(null);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const objectUrlRef = useRef<string | null>(null);
  const sessionIdRef = useRef<string>(makeId());
  const transitionRef = useRef(0);
  const mountedRef = useRef(true);

  const releaseAudio = useCallback(() => {
    const audio = audioRef.current;
    if (audio) {
      audio.onended = null;
      audio.onerror = null;
      audio.pause();
      audio.removeAttribute("src");
      audio.load();
      audioRef.current = null;
    }
    if (objectUrlRef.current) {
      URL.revokeObjectURL(objectUrlRef.current);
      objectUrlRef.current = null;
    }
  }, []);

  const playBlob = useCallback(async (blob: Blob) => {
    releaseAudio();
    const url = URL.createObjectURL(blob);
    objectUrlRef.current = url;
    const audio = new Audio(url);
    audioRef.current = audio;
    audio.onended = () => {
      if (audioRef.current === audio) {
        releaseAudio();
        setSnapshot({ status: "idle", message: "Prueba de voz completada.", error: null, needsGesture: false });
      }
    };
    audio.onerror = () => {
      if (audioRef.current === audio) {
        releaseAudio();
        setSnapshot({ status: "error", message: null, error: "El navegador no pudo reproducir el audio recibido.", needsGesture: false });
      }
    };

    try {
      await audio.play();
      if (mountedRef.current) setSnapshot({ status: "playing", message: "Reproduciendo audio de prueba…", error: null, needsGesture: false });
    } catch (error) {
      if (error instanceof DOMException && error.name === "NotAllowedError") {
        // Conserva el blob para que un segundo clic del usuario pueda reproducirlo.
        if (mountedRef.current) setSnapshot({ status: "idle", message: "El navegador bloqueó la reproducción automática. Pulsa «Reproducir audio».", error: null, needsGesture: true });
        return;
      }
      releaseAudio();
      if (mountedRef.current) setSnapshot({ status: "error", message: null, error: "No se pudo iniciar la reproducción de voz.", needsGesture: false });
    }
  }, [releaseAudio]);

  const testVoice = useCallback(async () => {
    if (abortRef.current) return;
    releaseAudio();
    const controller = new AbortController();
    abortRef.current = controller;
    setSnapshot({ status: "loading", message: "Generando audio de prueba…", error: null, needsGesture: false });
    const transitionId = transitionRef.current;
    transitionRef.current = (transitionRef.current + 1) % 2_147_483_647;

    try {
      const blob = await requestVoiceAudio({
        event_id: makeId(),
        session_id: sessionIdRef.current,
        class_name: "cautin",
        transition_id: transitionId,
      }, { signal: controller.signal });
      if (!controller.signal.aborted && mountedRef.current) await playBlob(blob);
    } catch (error) {
      if (controller.signal.aborted || (error instanceof VoiceError && error.code === "aborted")) return;
      if (!mountedRef.current) return;
      setSnapshot({ status: "error", message: null, error: error instanceof VoiceError ? error.userMessage : "No se pudo generar el audio de prueba.", needsGesture: false });
    } finally {
      if (abortRef.current === controller) abortRef.current = null;
    }
  }, [playBlob, releaseAudio]);

  const retryPlayback = useCallback(() => {
    const url = objectUrlRef.current;
    if (!url) return;
    const audio = audioRef.current;
    if (audio) {
      void audio.play().then(() => {
        setSnapshot({ status: "playing", message: "Reproduciendo audio de prueba…", error: null, needsGesture: false });
      }).catch(() => {
        setSnapshot({ status: "idle", message: "El navegador sigue bloqueando el audio. Comprueba el permiso de sonido e inténtalo otra vez.", error: null, needsGesture: true });
      });
    }
  }, []);

  const cancel = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    releaseAudio();
    setSnapshot(INITIAL);
  }, [releaseAudio]);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      abortRef.current?.abort();
      abortRef.current = null;
      releaseAudio();
    };
  }, [releaseAudio]);

  return { ...snapshot, testVoice, retryPlayback, cancel };
}
