"use client";

import { useEffect, useState } from "react";
import { BoxesLayer } from "@/components/BoxesLayer";
import { BackendUrlField } from "@/components/BackendUrlField";
import { CameraView } from "@/components/CameraView";
import { CaptureControls } from "@/components/CaptureControls";
import { HudOverlay } from "@/components/HudOverlay";
import { VoiceTestButton } from "@/components/VoiceTestButton";
import { useCamera } from "@/hooks/useCamera";
import { useCapture } from "@/hooks/useCapture";
import { useLiveDetection } from "@/hooks/useLiveDetection";

const STORAGE_KEY = "toollighthouse:backend-url";

/**
 * Punto de unión del lado cliente: la cámara y la captura comparten el mismo videoRef.
 * `allowBackendUrl` lo decide el servidor (ALLOW_CLIENT_BACKEND_URL).
 */
export function CameraPanel({ allowBackendUrl = false }: { allowBackendUrl?: boolean }) {
  const camera = useCamera();
  const [backendUrl, setBackendUrl] = useState("");
  // Fase 3: cajas sobre el video y modelo en el HUD.
  const [showDetails, setShowDetails] = useState(false);
  const effectiveBackendUrl = allowBackendUrl ? backendUrl : undefined;
  const capture = useCapture(camera.videoRef, effectiveBackendUrl);
  const live = useLiveDetection(camera.videoRef, camera.status === "active", effectiveBackendUrl);

  // Recordar la última URL usada. El almacenamiento puede no estar disponible.
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
            <HudOverlay
              captureStatus={capture.status}
              result={capture.result}
              live={live}
              showDetails={showDetails}
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
      <VoiceTestButton />
    </div>
  );
}
