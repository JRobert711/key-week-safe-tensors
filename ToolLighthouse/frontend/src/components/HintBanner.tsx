"use client";

import type { HintFeedbackStatus, HintsStatus, LowConfidenceHint } from "@/types/hints";

interface HintBannerProps {
  hint: LowConfidenceHint | null;
  status: HintsStatus;
  onDismiss: () => void;
  /** Estado del reporte "No es eso". */
  feedback: HintFeedbackStatus;
  onFalsePositive: () => void;
}

const FEEDBACK_NOTES: Partial<Record<HintFeedbackStatus, string>> = {
  sending: "Anotando…",
  sent: "Anotado, no volveré a avisar de esto por un rato.",
  error: "No se pudo enviar el reporte. La detección sigue normal.",
};

/**
 * Aviso "Creo haber visto X" sobre el video. El texto lo construye el backend (tabla cerrada) y se muestra
 * como texto plano. role="status": es informativo, no debe interrumpir a un lector de pantalla.
 * "No es eso" cierra el banner y reporta un falso positivo; la nota de resultado vive fuera del banner.
 */
export function HintBanner({ hint, status, onDismiss, feedback, onFalsePositive }: HintBannerProps) {
  const note = FEEDBACK_NOTES[feedback];
  return (
    <div className="hint-layer">
      {status === "reconnecting" && !hint && (
        <p className="hint-conn" role="status">Avisos: reconectando…</p>
      )}
      <div role="status" aria-live="polite">
        {hint && (
          <div className="hint-banner" key={hint.hint_id}>
            <p className="hint-text">{hint.message}</p>
            <div className="hint-actions">
              {feedback !== "unavailable" && (
                <button type="button" onClick={onFalsePositive} disabled={feedback === "sending"}>
                  No es eso
                </button>
              )}
              <button type="button" className="secondary" onClick={onDismiss}>
                Cerrar
              </button>
            </div>
          </div>
        )}
        {!hint && note && (
          <p className={feedback === "error" ? "hint-note hint-note-error" : "hint-note"}>{note}</p>
        )}
      </div>
    </div>
  );
}
