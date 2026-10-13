"use client";

import type { UseCapture } from "@/hooks/useCapture";
import type { UseLiveDetection } from "@/hooks/useLiveDetection";

interface Props {
  capture: UseCapture;
  live: UseLiveDetection;
  cameraActive: boolean;
}

export function CaptureControls({ capture, live, cameraActive }: Props) {
  const { status, error, busy, captureAndSend } = capture;

  return (
    <div className="capture">
      <div className="controls">
        <button type="button" onClick={() => void captureAndSend()} disabled={!cameraActive || busy || live.running}>
          {busy ? "Enviando…" : "Capturar y enviar"}
        </button>
        <button
          type="button"
          className="secondary"
          onClick={() => (live.running ? live.stop() : live.start())}
          disabled={!cameraActive}
        >
          {live.running ? "Detener modo en vivo" : "Iniciar modo en vivo"}
        </button>
        {!cameraActive && <span className="hint">Inicia la cámara para capturar.</span>}
        {live.running && <span className="hint">Modo en vivo activo: la captura manual está desactivada.</span>}
      </div>

      <div role="status" aria-live="polite">
        {status === "capturing" && <p className="status">Capturando fotograma…</p>}
        {status === "sending" && <p className="status">Enviando al servidor…</p>}
      </div>

      {status === "error" && error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
