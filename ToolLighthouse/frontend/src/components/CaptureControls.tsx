"use client";

import type { UseCapture } from "@/hooks/useCapture";

function formatClass(name: string): string {
  return name.replace(/_/g, " ");
}

interface Props {
  capture: UseCapture;
  cameraActive: boolean;
}

export function CaptureControls({ capture, cameraActive }: Props) {
  const { status, result, error, busy, captureAndSend } = capture;

  return (
    <div className="capture">
      <div className="controls">
        <button type="button" onClick={() => void captureAndSend()} disabled={!cameraActive || busy}>
          {busy ? "Enviando…" : "Capturar y enviar"}
        </button>
        {!cameraActive && <span className="hint">Inicia la cámara para capturar.</span>}
      </div>

      <div role="status" aria-live="polite">
        {status === "capturing" && <p className="status">Capturando fotograma…</p>}
        {status === "sending" && <p className="status">Enviando al servidor…</p>}
        {status === "success" && result && (
          <p className="result">
            {result.class ? (
              <>
                <strong>{formatClass(result.class)}</strong>
                {result.confidence !== null && ` · confianza ${Math.round(result.confidence * 100)} %`}
              </>
            ) : (
              "No se detectó ninguna herramienta en este fotograma."
            )}
          </p>
        )}
      </div>

      {status === "error" && error && (
        <p className="alert" role="alert">
          {error}
        </p>
      )}
    </div>
  );
}
