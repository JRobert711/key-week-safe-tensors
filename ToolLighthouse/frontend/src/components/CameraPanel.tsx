"use client";

import { useEffect, useState } from "react";
import { BoxesLayer } from "@/components/BoxesLayer";
import { BackendUrlField } from "@/components/BackendUrlField";
import { CameraView } from "@/components/CameraView";
import { CaptureControls } from "@/components/CaptureControls";
import { HintBanner } from "@/components/HintBanner";
import { HudOverlay } from "@/components/HudOverlay";
import { VoiceControls } from "@/components/VoiceControls";
import { VoiceTestButton } from "@/components/VoiceTestButton";
import { useCamera } from "@/hooks/useCamera";
import { useCapture } from "@/hooks/useCapture";
import { useHints } from "@/hooks/useHints";
import { useLiveDetection } from "@/hooks/useLiveDetection";
import { useVoiceAnnouncer } from "@/hooks/useVoiceAnnouncer";

const STORAGE_KEY = "toollighthouse:backend-url";

/** Punto de unión del lado cliente: cámara, detección en vivo y voz automática. */
export function CameraPanel({ allowBackendUrl = false }: { allowBackendUrl?: boolean }) {
  const camera = useCamera();
  const [backendUrl, setBackendUrl] = useState("");
  const [showDetails, setShowDetails] = useState(false);
  const [voiceEnabled, setVoiceEnabled] = useState(true);
  const effectiveBackendUrl = allowBackendUrl ? backendUrl : undefined;
  const capture = useCapture(camera.videoRef, effectiveBackendUrl);
  const live = useLiveDetection(camera.videoRef, camera.status === "active", effectiveBackendUrl);
  // Los avisos solo existen mientras el modo en vivo envía fotogramas; la conexión sigue ese mismo ciclo.
  const hints = useHints({ enabled: live.running });
  const voice = useVoiceAnnouncer({
    boxes: live.boxes,
    model: live.model,
    phase: live.phase,
    running: live.running,
    enabled: voiceEnabled,
    hintId: hints.hint?.hint_id ?? null,
  });
  // Cerrar o reportar un aviso también corta su voz (la de detecciones no se toca).
  const { cancelHint } = voice;
  const dismissHint = () => {
    cancelHint();
    hints.dismiss();
  };
  const reportHint = () => {
    cancelHint();
    hints.reportFalsePositive();
  };

  useEffect(() => {
    if (!allowBackendUrl) return;
    try {
      setBackendUrl(window.localStorage.getItem(STORAGE_KEY) ?? "");
    } catch {
      /* sin almacenamiento: se empieza vacío */
    }
  }, [allowBackendUrl]);

  function handleBackendUrlChange(value: string) {
    setBackendUrl(value);
    try {
      window.localStorage.setItem(STORAGE_KEY, value);
    } catch {
      /* ignorar */
    }
  }

  const boxes = live.running
    ? (live.boxes ?? [])
    : capture.status === "success"
      ? (capture.result?.boxes ?? [])
      : [];

  return (
    <div className="panel">
      <CameraView
        camera={camera}
        overlay={
          <>
            {showDetails && <BoxesLayer videoRef={camera.videoRef} boxes={boxes} />}
            <HudOverlay captureStatus={capture.status} result={capture.result} live={live} showDetails={showDetails} />
            <HintBanner
              hint={hints.hint}
              status={hints.status}
              onDismiss={dismissHint}
              feedback={hints.feedback}
              onFalsePositive={reportHint}
            />
          </>
        }
      />
      <CaptureControls
        capture={capture}
        live={live}
        cameraActive={camera.status === "active"}
        showDetails={showDetails}
        onToggleDetails={() => setShowDetails((v) => !v)}
      />
      <VoiceControls enabled={voiceEnabled} onEnabledChange={setVoiceEnabled} state={voice} onRetryPlayback={voice.retryPlayback} />
      <VoiceTestButton />
    </div>
  );
}
