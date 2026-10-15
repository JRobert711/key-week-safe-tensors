"use client";

import type { VoiceAnnouncerState } from "@/hooks/useVoiceAnnouncer";

interface VoiceControlsProps {
  enabled: boolean;
  onEnabledChange: (enabled: boolean) => void;
  state: VoiceAnnouncerState;
  onRetryPlayback: () => void;
}

const CLASS_LABELS: Record<string, string> = {
  cautin: "cautín",
  multimetro_digital: "multímetro digital",
  multimetro_analogico: "multímetro analógico",
  estacion_soldadura: "estación de soldadura",
  caja_sensores: "caja de sensores",
  ventilador_pasco: "ventilador Pasco",
  bascula: "báscula",
  capacitor_variable: "capacitor variable",
  fuente_voltaje: "fuente de voltaje",
};

export function VoiceControls({ enabled, onEnabledChange, state, onRetryPlayback }: VoiceControlsProps) {
  let statusMessage: string;
  if (!enabled) {
    statusMessage = "Los anuncios automáticos están desactivados.";
  } else if (state.needsGesture) {
    statusMessage = "El navegador necesita una acción para reproducir el audio.";
  } else {
    switch (state.status) {
      case "loading": statusMessage = "Generando el anuncio de voz…"; break;
      case "playing": statusMessage = "Reproduciendo anuncio de voz…"; break;
      case "error": statusMessage = "No se pudo completar el anuncio de voz."; break;
      default: statusMessage = "Esperando un cambio en el conjunto de objetos detectados.";
    }
  }

  const lastLabels = state.lastAnnounced?.map((name) => CLASS_LABELS[name] ?? name).join(", ");

  return (
    <section className="voice-controls" aria-label="Controles de anuncios automáticos">
      <label>
        <input type="checkbox" checked={enabled} onChange={(event) => onEnabledChange(event.currentTarget.checked)} />
        {" "}Anuncios automáticos por voz
      </label>
      <p role="status" aria-live="polite">{statusMessage}</p>
      {enabled && state.needsGesture && (
        <button type="button" onClick={onRetryPlayback}>Reproducir anuncio pendiente</button>
      )}
      {enabled && state.error && <p role="alert">{state.error}</p>}
      {lastLabels && <p>Último conjunto anunciado: {lastLabels}</p>}
    </section>
  );
}
