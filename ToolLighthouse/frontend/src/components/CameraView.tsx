"use client";

import type { ReactNode } from "react";
import type { UseCamera } from "@/hooks/useCamera";

const STATUS_LABEL: Record<UseCamera["status"], string> = {
  idle: "Cámara apagada",
  requesting: "Esperando permiso de la cámara…",
  active: "Cámara activa",
  denied: "Permiso denegado",
  unavailable: "Cámara no disponible",
  error: "Error de cámara",
};

/** `overlay` se dibuja encima del video solo mientras la cámara está activa (p. ej. el HUD). */
export function CameraView({ camera, overlay }: { camera: UseCamera; overlay?: ReactNode }) {
  const { videoRef, status, message, devices, selectedDeviceId, start, stop, selectDevice } = camera;
  const isActive = status === "active";
  const isBusy = status === "requesting";
  const isProblem = status === "denied" || status === "unavailable" || status === "error";

  return (
    <div className="camera">
      <div className="viewport">
        <video ref={videoRef} className="video" playsInline muted autoPlay hidden={!isActive} />
        {!isActive && (
          <p className="placeholder" aria-hidden={isProblem}>
            {isBusy ? STATUS_LABEL.requesting : "Pulsa Iniciar cámara para ver el video."}
          </p>
        )}
        {isActive && overlay}
      </div>

      <p className="status" data-status={status} role="status">
        {STATUS_LABEL[status]}
      </p>

      {isProblem && message && (
        <p className="alert" role="alert">
          {message}
        </p>
      )}

      <div className="controls">
        {isActive ? (
          <button type="button" onClick={stop}>
            Detener cámara
          </button>
        ) : (
          <button type="button" onClick={() => void start()} disabled={isBusy}>
            {isProblem ? "Reintentar" : "Iniciar cámara"}
          </button>
        )}

        {devices.length > 1 && (
          <label className="picker">
            <span>Cámara</span>
            <select
              value={selectedDeviceId ?? ""}
              onChange={(e) => void selectDevice(e.target.value)}
              disabled={isBusy}
            >
              {devices.map((d) => (
                <option key={d.deviceId} value={d.deviceId}>
                  {d.label}
                </option>
              ))}
            </select>
          </label>
        )}
      </div>
    </div>
  );
}
