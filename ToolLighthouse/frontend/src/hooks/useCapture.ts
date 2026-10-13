"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { CaptureError, captureFrame } from "@/lib/captureFrame";
import { SendError, sendFrame } from "@/lib/captureApi";
import type { DetectionResult } from "@/types/detection";

export type CaptureStatus = "idle" | "capturing" | "sending" | "success" | "error";

export interface UseCapture {
  status: CaptureStatus;
  result: DetectionResult | null;
  error: string | null;
  /** true mientras hay un envío en curso; sirve para deshabilitar el botón. */
  busy: boolean;
  captureAndSend: () => Promise<void>;
}

export function useCapture(
  videoRef: React.RefObject<HTMLVideoElement | null>,
  /** URL del backend escrita por el usuario; si está vacía, el servidor usa BACKEND_URL. */
  backendUrl?: string,
): UseCapture {
  const [status, setStatus] = useState<CaptureStatus>("idle");
  const [result, setResult] = useState<DetectionResult | null>(null);
  const [error, setError] = useState<string | null>(null);

  // El ref se actualiza al instante; el estado de React no, así que un doble clic
  // rápido no podría bloquearse solo con `status`.
  const inFlightRef = useRef(false);
  const abortRef = useRef<AbortController | null>(null);

  const captureAndSend = useCallback(async () => {
    if (inFlightRef.current) return;
    const video = videoRef.current;
    if (!video) return;

    inFlightRef.current = true;
    const controller = new AbortController();
    abortRef.current = controller;
    setResult(null);
    setError(null);

    try {
      setStatus("capturing");
      const frame = await captureFrame(video);
      if (controller.signal.aborted) return;

      setStatus("sending");
      const detection = await sendFrame(frame, { signal: controller.signal, backendUrl });
      setResult(detection);
      setStatus("success");
    } catch (e) {
      if (e instanceof SendError && e.code === "aborted") return;
      setError(
        e instanceof CaptureError || e instanceof SendError
          ? e.userMessage
          : "Ocurrió un error inesperado. Vuelve a intentarlo.",
      );
      setStatus("error");
    } finally {
      inFlightRef.current = false;
      abortRef.current = null;
    }
  }, [videoRef, backendUrl]);

  // Cancelar un envío pendiente al salir de la página.
  useEffect(() => () => abortRef.current?.abort(), []);

  return {
    status,
    result,
    error,
    busy: status === "capturing" || status === "sending",
    captureAndSend,
  };
}
