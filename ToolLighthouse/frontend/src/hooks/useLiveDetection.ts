"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CaptureError, captureFrame } from "@/lib/captureFrame";
import { SendError, sendFrame } from "@/lib/captureApi";
import { evaluate, pushSample, type Sample } from "@/lib/stability";
import { userMessageOf } from "@/lib/userMessage";

// Parámetros (equivalentes a los de detect.py).
export const HISTORY_SIZE = 10;
export const MIN_FRAMES = 7;
const MAX_WIDTH = 640;
const FPS_SMOOTHING = 0.9; // fps = 0.9 * fps + 0.1 * instantáneo
const RETRY_BASE_MS = 1_000;
const RETRY_MAX_MS = 5_000;

export type LivePhase = "idle" | "starting" | "running" | "retrying" | "paused";

interface LiveSnapshot {
  phase: LivePhase;
  /** Clase oficial tras el filtro (>= MIN_FRAMES de los últimos HISTORY_SIZE), o null = REPOSO. */
  official: string | null;
  confidence: number | null;
  /** Clase más frecuente del historial y cuántas veces aparece (para "Estabilidad: X/10"). */
  candidate: string | null;
  count: number;
  fps: number | null;
  error: string | null;
}

export interface UseLiveDetection extends LiveSnapshot {
  running: boolean;
  historySize: number;
  minFrames: number;
  start: () => void;
  stop: () => void;
}

const INITIAL: LiveSnapshot = {
  phase: "idle",
  official: null,
  confidence: null,
  candidate: null,
  count: 0,
  fps: null,
  error: null,
};

/** Espera `ms` o hasta que se cancele; nunca rechaza. */
function sleep(ms: number, signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted) return resolve();
    const done = () => {
      clearTimeout(timer);
      signal.removeEventListener("abort", done);
      resolve();
    };
    const timer = setTimeout(done, ms);
    signal.addEventListener("abort", done, { once: true });
  });
}

/** Espera a que la pestaña vuelva a ser visible o a que se cancele. */
function waitUntilVisible(signal: AbortSignal): Promise<void> {
  return new Promise((resolve) => {
    if (signal.aborted || document.visibilityState === "visible") return resolve();
    const done = () => {
      document.removeEventListener("visibilitychange", onChange);
      signal.removeEventListener("abort", done);
      resolve();
    };
    const onChange = () => {
      if (document.visibilityState === "visible") done();
    };
    document.addEventListener("visibilitychange", onChange);
    signal.addEventListener("abort", done, { once: true });
  });
}

/**
 * Modo en vivo: bucle secuencial captura -> envío -> respuesta -> siguiente fotograma.
 * Reutiliza captureFrame y sendFrame; no usa setInterval, así que nunca hay dos peticiones a la vez.
 */
export function useLiveDetection(
  videoRef: React.RefObject<HTMLVideoElement | null>,
  /** El bucle se detiene solo cuando la cámara deja de estar activa. */
  cameraActive: boolean,
  backendUrl?: string,
): UseLiveDetection {
  const [snapshot, setSnapshot] = useState<LiveSnapshot>(INITIAL);
  const abortRef = useRef<AbortController | null>(null);

  // El bucle lee siempre la URL más reciente sin reiniciarse.
  const backendUrlRef = useRef(backendUrl);
  useEffect(() => {
    backendUrlRef.current = backendUrl;
  }, [backendUrl]);

  const stop = useCallback(() => {
    abortRef.current?.abort();
    abortRef.current = null;
    setSnapshot(INITIAL);
  }, []);

  const start = useCallback(() => {
    if (abortRef.current) return;
    const controller = new AbortController();
    const { signal } = controller;
    abortRef.current = controller;
    setSnapshot({ ...INITIAL, phase: "starting" });

    async function run() {
      let history: Sample[] = [];
      let fps: number | null = null;
      let lastDone: number | null = null;
      let failures = 0;

      while (!signal.aborted) {
        if (document.visibilityState === "hidden") {
          setSnapshot((prev) => ({ ...prev, phase: "paused" }));
          await waitUntilVisible(signal);
          lastDone = null; // la pausa no cuenta para los FPS
          continue;
        }

        try {
          const video = videoRef.current;
          if (!video) throw new CaptureError("video-not-ready", "La cámara todavía no está lista.");
          const frame = await captureFrame(video, { maxWidth: MAX_WIDTH });
          if (signal.aborted) return;

          const detection = await sendFrame(frame, { signal, backendUrl: backendUrlRef.current });
          if (signal.aborted) return;

          failures = 0;
          const now = performance.now();
          if (lastDone !== null && now > lastDone) {
            const instant = 1000 / (now - lastDone);
            fps = fps === null ? instant : FPS_SMOOTHING * fps + (1 - FPS_SMOOTHING) * instant;
          }
          lastDone = now;

          history = pushSample(history, { class: detection.class, confidence: detection.confidence }, HISTORY_SIZE);
          const { official, confidence, candidate, count } = evaluate(history, MIN_FRAMES);
          setSnapshot({ phase: "running", official, confidence, candidate, count, fps, error: null });
        } catch (e) {
          if (signal.aborted || (e instanceof SendError && e.code === "aborted")) return;

          // Sin respuesta fiable no se mantiene un estado "activo" antiguo.
          history = [];
          fps = null;
          lastDone = null;
          failures += 1;
          setSnapshot({ ...INITIAL, phase: "retrying", error: userMessageOf(e) });
          await sleep(Math.min(RETRY_BASE_MS * 2 ** (failures - 1), RETRY_MAX_MS), signal);
        }
      }
    }

    void run().finally(() => {
      if (abortRef.current === controller) abortRef.current = null;
    });
  }, [videoRef]);

  // Al detener la cámara se corta el bucle; al desmontar se cancela la petición pendiente.
  useEffect(() => {
    if (!cameraActive) stop();
  }, [cameraActive, stop]);
  useEffect(() => () => abortRef.current?.abort(), []);

  return {
    ...snapshot,
    running: snapshot.phase !== "idle",
    historySize: HISTORY_SIZE,
    minFrames: MIN_FRAMES,
    start,
    stop,
  };
}
