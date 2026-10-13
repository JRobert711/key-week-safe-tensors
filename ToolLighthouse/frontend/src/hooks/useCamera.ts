"use client";

import { useCallback, useEffect, useRef, useState } from "react";

export type CameraStatus =
  | "idle" // cámara apagada
  | "requesting" // esperando permiso o arrancando el dispositivo
  | "active" // transmitiendo video
  | "denied" // permiso rechazado
  | "unavailable" // sin cámara, sin soporte o contexto no seguro
  | "error"; // cámara ocupada, desconectada u otro fallo

export interface CameraDevice {
  deviceId: string;
  label: string;
}

export interface UseCamera {
  videoRef: React.RefObject<HTMLVideoElement | null>;
  status: CameraStatus;
  /** Mensaje legible para mostrar al usuario cuando status es denied, unavailable o error. */
  message: string | null;
  devices: CameraDevice[];
  selectedDeviceId: string | null;
  start: () => Promise<void>;
  stop: () => void;
  selectDevice: (deviceId: string) => Promise<void>;
}

interface Failure {
  status: CameraStatus;
  message: string;
}

function describeError(error: unknown): Failure {
  const name = error instanceof DOMException ? error.name : "";
  switch (name) {
    case "NotAllowedError":
    case "SecurityError":
      return {
        status: "denied",
        message:
          "El navegador bloqueó el acceso a la cámara. Permítelo desde el icono de candado junto a la dirección y pulsa Iniciar cámara de nuevo.",
      };
    case "NotFoundError":
    case "DevicesNotFoundError":
      return {
        status: "unavailable",
        message: "No se encontró ninguna cámara. Conecta una y vuelve a intentarlo.",
      };
    case "NotReadableError":
    case "TrackStartError":
      return {
        status: "error",
        message:
          "No se pudo abrir la cámara porque otra aplicación o pestaña la está usando. Ciérrala y vuelve a intentarlo.",
      };
    case "AbortError":
      return {
        status: "error",
        message: "El navegador interrumpió el arranque de la cámara. Vuelve a intentarlo.",
      };
    default:
      return {
        status: "error",
        message: "No se pudo iniciar la cámara. Vuelve a intentarlo.",
      };
  }
}

export function useCamera(): UseCamera {
  const videoRef = useRef<HTMLVideoElement | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  // Cada arranque o parada incrementa el contador; una respuesta tardía de un
  // intento anterior se descarta y sus pistas se liberan.
  const requestIdRef = useRef(0);

  const [status, setStatus] = useState<CameraStatus>("idle");
  const [message, setMessage] = useState<string | null>(null);
  const [devices, setDevices] = useState<CameraDevice[]>([]);
  const [selectedDeviceId, setSelectedDeviceId] = useState<string | null>(null);

  const releaseStream = useCallback(() => {
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (videoRef.current) videoRef.current.srcObject = null;
  }, []);

  const refreshDevices = useCallback(async () => {
    if (!navigator.mediaDevices?.enumerateDevices) return;
    try {
      const all = await navigator.mediaDevices.enumerateDevices();
      setDevices(
        all
          .filter((d) => d.kind === "videoinput")
          .map((d, i) => ({ deviceId: d.deviceId, label: d.label || `Cámara ${i + 1}` })),
      );
    } catch {
      // La lista de cámaras es opcional; si falla, la cámara sigue funcionando.
    }
  }, []);

  const stop = useCallback(() => {
    requestIdRef.current += 1;
    releaseStream();
    setStatus("idle");
    setMessage(null);
  }, [releaseStream]);

  const open = useCallback(
    async (deviceId: string | null) => {
      const requestId = ++requestIdRef.current;
      releaseStream();

      if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
        setStatus("unavailable");
        setMessage(
          "Este navegador no permite usar la cámara en esta dirección. Abre la aplicación con HTTPS o desde localhost.",
        );
        return;
      }

      setStatus("requesting");
      setMessage(null);

      const attempt = (id: string | null) =>
        navigator.mediaDevices.getUserMedia({
          video: id ? { deviceId: { exact: id } } : { facingMode: { ideal: "environment" } },
          audio: false,
        });

      let stream: MediaStream;
      try {
        try {
          stream = await attempt(deviceId);
        } catch (error) {
          // La cámara elegida ya no existe: reintentar con la predeterminada.
          if (deviceId && error instanceof DOMException && error.name === "OverconstrainedError") {
            stream = await attempt(null);
          } else {
            throw error;
          }
        }
      } catch (error) {
        if (requestId !== requestIdRef.current) return;
        const failure = describeError(error);
        setStatus(failure.status);
        setMessage(failure.message);
        return;
      }

      if (requestId !== requestIdRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }

      streamRef.current = stream;
      const track = stream.getVideoTracks()[0];
      track?.addEventListener("ended", () => {
        if (streamRef.current !== stream) return;
        releaseStream();
        setStatus("error");
        setMessage("La cámara se desconectó. Revisa la conexión y pulsa Iniciar cámara.");
      });
      setSelectedDeviceId(track?.getSettings().deviceId ?? deviceId);

      const video = videoRef.current;
      if (video) {
        video.srcObject = stream;
        try {
          await video.play();
        } catch {
          // play() puede rechazarse si se detiene mientras arranca; el estado lo decide el contador.
        }
      }
      if (requestId !== requestIdRef.current) return;

      setStatus("active");
      // Las etiquetas de las cámaras solo están disponibles tras conceder el permiso.
      void refreshDevices();
    },
    [refreshDevices, releaseStream],
  );

  const start = useCallback(() => open(selectedDeviceId), [open, selectedDeviceId]);

  const selectDevice = useCallback(
    async (deviceId: string) => {
      setSelectedDeviceId(deviceId);
      if (streamRef.current) await open(deviceId);
    },
    [open],
  );

  // Mantener actualizada la lista cuando se conecta o desconecta una cámara.
  useEffect(() => {
    const media = navigator.mediaDevices;
    if (!media?.addEventListener) return;
    const onChange = () => void refreshDevices();
    media.addEventListener("devicechange", onChange);
    return () => media.removeEventListener("devicechange", onChange);
  }, [refreshDevices]);

  // Al desmontar (cambio de página o cierre), liberar la cámara.
  useEffect(() => {
    return () => {
      requestIdRef.current += 1;
      streamRef.current?.getTracks().forEach((track) => track.stop());
      streamRef.current = null;
    };
  }, []);

  return { videoRef, status, message, devices, selectedDeviceId, start, stop, selectDevice };
}
