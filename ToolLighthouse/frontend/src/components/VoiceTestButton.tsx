"use client";

import { useVoice } from "@/hooks/useVoice";

/** Control manual de prueba; no está conectado a las detecciones automáticas. */
export function VoiceTestButton() {
  const voice = useVoice();
  const busy = voice.status === "loading" || voice.status === "playing";

  return (
    <section className="voice-test" aria-label="Prueba de voz">
      <div className="controls">
        <button type="button" className="secondary" onClick={() => void voice.testVoice()} disabled={busy}>
          {voice.status === "loading" ? "Generando audio…" : "Probar voz"}
        </button>
        {voice.needsGesture && (
          <button type="button" className="secondary" onClick={voice.retryPlayback}>
            Reproducir audio
          </button>
        )}
        {voice.status === "playing" && (
          <button type="button" className="secondary" onClick={voice.cancel}>
            Detener audio
          </button>
        )}
      </div>
      <div role="status" aria-live="polite">
        {voice.message && <p className="status">{voice.message}</p>}
      </div>
      {voice.status === "error" && voice.error && <p className="alert" role="alert">{voice.error}</p>}
    </section>
  );
}
