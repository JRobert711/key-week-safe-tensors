"use client";

import type { CaptureStatus } from "@/hooks/useCapture";
import type { UseLiveDetection } from "@/hooks/useLiveDetection";
import { formatClass } from "@/lib/formatClass";
import type { DetectionResult, ModelKind } from "@/types/detection";

type Tone = "active" | "idle" | "busy" | "error";

interface Props {
  captureStatus: CaptureStatus;
  result: DetectionResult | null;
  live: UseLiveDetection;
  /** Fase 3: muestra el modelo (y avisa si el backend no lo informa). */
  showDetails?: boolean;
}

function describeLive(live: UseLiveDetection): { label: string; tone: Tone } {
  switch (live.phase) {
    case "starting":
      return { label: "INICIANDO…", tone: "busy" };
    case "retrying":
      return { label: "SIN RESPUESTA DEL SERVIDOR", tone: "error" };
    case "paused":
      return { label: "EN PAUSA (pestaña oculta)", tone: "idle" };
    default:
      return live.official
        ? { label: `ACTIVO: ${formatClass(live.official).toUpperCase()}`, tone: "active" }
        : { label: "ESTADO: REPOSO (Ninguna)", tone: "idle" };
  }
}

function describeManual({ captureStatus, result }: Pick<Props, "captureStatus" | "result">): {
  label: string;
  tone: Tone;
} {
  if (captureStatus === "capturing" || captureStatus === "sending") {
    return { label: "ANALIZANDO…", tone: "busy" };
  }
  if (captureStatus === "error") {
    return { label: "ERROR AL ANALIZAR", tone: "error" };
  }
  if (captureStatus === "success" && result) {
    return result.class
      ? { label: `ACTIVO: ${formatClass(result.class).toUpperCase()}`, tone: "active" }
      : { label: "ESTADO: REPOSO (Ninguna)", tone: "idle" };
  }
  return { label: "ESPERANDO CAPTURA", tone: "idle" };
}

/** Panel de telemetría sobre el video, equivalente al HUD de draw_hud en detect.py. */
export function HudOverlay({ captureStatus, result, live, showDetails = false }: Props) {
  const { label, tone } = live.running ? describeLive(live) : describeManual({ captureStatus, result });
  const showLiveStats = live.running && live.phase !== "starting" && live.phase !== "retrying";

  // Modelo del último resultado disponible (null = todavía no hay respuesta).
  const hasResponse = live.running ? live.phase === "running" : captureStatus === "success" && result !== null;
  const model: ModelKind | null = live.running ? live.model : (result?.model ?? null);
  const modelLine = showDetails && hasResponse && (
    <p className="hud-meta">Modelo: {model ? model.toUpperCase() : "no informado por el backend"}</p>
  );

  return (
    <div className="hud">
      <p className="hud-title">ToolLighthouse | Detector 3D</p>
      <p className="hud-status" data-tone={tone} role="status" aria-live="polite">
        {label}
      </p>

      {live.running ? (
        <>
          {tone === "active" && live.confidence !== null && (
            <p className="hud-meta">Confianza: {Math.round(live.confidence * 100)} %</p>
          )}
          {showLiveStats && (
            <>
              <p className="hud-meta">
                Estabilidad: {live.count}/{live.historySize} (mín. {live.minFrames})
                {live.candidate && !live.official && ` · ${formatClass(live.candidate)}`}
              </p>
              <p className="hud-meta">FPS: {live.fps !== null ? live.fps.toFixed(1) : "—"}</p>
              {modelLine}
            </>
          )}
          {live.error && (
            <p className="hud-meta" data-tone="error">
              {live.error} Reintentando…
            </p>
          )}
        </>
      ) : (
        tone === "active" &&
        result && (
          <p className="hud-meta">
            {result.confidence !== null && `Confianza: ${Math.round(result.confidence * 100)} %`}
            {result.detections > 1 && ` · ${result.detections} detecciones`}
          </p>
        )
      )}
      {!live.running && modelLine}
    </div>
  );
}
