"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { HintsError, requestHintSocketUrl, sendHintFalsePositive } from "@/lib/hintsApi";
import { HintsConnection } from "@/lib/hintsConnection";
import type { HintFeedbackStatus, HintServerMessage, HintsStatus, LowConfidenceHint } from "@/types/hints";

/** Tiempo mínimo que un aviso permanece visible aunque el backend lo cierre enseguida. */
export const MIN_VISIBLE_MS = 4_000;
/** Tiempo que se muestra la nota "Anotado..." (o el error) tras pulsar "No es eso". */
export const FEEDBACK_NOTE_MS = 6_000;

export interface UseHints {
  status: HintsStatus;
  /** Aviso activo o null. Solo hay uno a la vez: un aviso nuevo reemplaza al anterior. */
  hint: LowConfidenceHint | null;
  /** Oculta el aviso actual (no afecta a los siguientes). */
  dismiss: () => void;
  /** Estado del reporte "No es eso". "unavailable" = el backend no lo ofrece: se oculta el botón. */
  feedback: HintFeedbackStatus;
  /** Marca el aviso actual como falso positivo: cierra el banner y avisa al backend (no afecta a la detección). */
  reportFalsePositive: () => void;
}

interface Shown {
  hint: LowConfidenceHint;
  shownAt: number;
}

/**
 * Recibe los avisos "Creo haber visto X" del backend por WebSocket.
 * Se conecta solo mientras `enabled` es true (p. ej. modo en vivo activo) y se desconecta al apagarse.
 * Un fallo aquí jamás afecta a la detección: todo error termina en reconexión o en estado "disabled".
 */
export function useHints({ enabled }: { enabled: boolean }): UseHints {
  const [status, setStatus] = useState<HintsStatus>("idle");
  const [shown, setShown] = useState<Shown | null>(null);
  const shownRef = useRef<Shown | null>(null);
  const clearTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const statusRef = useRef<HintsStatus>("idle");
  const [feedback, setFeedback] = useState<HintFeedbackStatus>("idle");
  const feedbackRef = useRef<HintFeedbackStatus>("idle");
  const feedbackTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const feedbackAbortRef = useRef<AbortController | null>(null);

  const show = useCallback((value: Shown | null) => {
    shownRef.current = value;
    setShown(value);
  }, []);

  const cancelPendingClear = useCallback(() => {
    if (clearTimerRef.current !== null) {
      clearTimeout(clearTimerRef.current);
      clearTimerRef.current = null;
    }
  }, []);

  const dismiss = useCallback(() => {
    cancelPendingClear();
    show(null);
  }, [cancelPendingClear, show]);

  const updateFeedback = useCallback((next: HintFeedbackStatus) => {
    feedbackRef.current = next;
    setFeedback(next);
  }, []);

  const clearFeedbackTimer = useCallback(() => {
    if (feedbackTimerRef.current !== null) {
      clearTimeout(feedbackTimerRef.current);
      feedbackTimerRef.current = null;
    }
  }, []);

  const reportFalsePositive = useCallback(() => {
    const current = shownRef.current;
    if (!current || feedbackRef.current === "sending" || feedbackRef.current === "unavailable") return;
    const hintId = current.hint.hint_id;

    // El banner se cierra al instante; el resultado del envío solo cambia la nota.
    cancelPendingClear();
    show(null);
    clearFeedbackTimer();
    feedbackAbortRef.current?.abort();
    const controller = new AbortController();
    feedbackAbortRef.current = controller;
    updateFeedback("sending");

    const finish = (next: HintFeedbackStatus) => {
      if (controller.signal.aborted) return;
      feedbackAbortRef.current = null;
      updateFeedback(next);
      if (next === "unavailable") return; // permanente: no hay nada que ocultar luego
      feedbackTimerRef.current = setTimeout(() => {
        feedbackTimerRef.current = null;
        updateFeedback("idle");
      }, FEEDBACK_NOTE_MS);
    };

    sendHintFalsePositive(hintId, { signal: controller.signal }).then(
      () => finish("sent"),
      (error: unknown) => {
        // "disabled" = el backend no ofrece feedback: se oculta el botón el resto de la sesión.
        finish(error instanceof HintsError && error.code === "disabled" ? "unavailable" : "error");
      },
    );
  }, [cancelPendingClear, clearFeedbackTimer, show, updateFeedback]);

  // Al apagarse el modo en vivo o desmontar se cancela cualquier envío o nota pendiente.
  useEffect(() => {
    if (enabled) return;
    feedbackAbortRef.current?.abort();
    feedbackAbortRef.current = null;
    clearFeedbackTimer();
    if (feedbackRef.current !== "unavailable") updateFeedback("idle");
  }, [enabled, clearFeedbackTimer, updateFeedback]);

  useEffect(
    () => () => {
      feedbackAbortRef.current?.abort();
      clearFeedbackTimer();
    },
    [clearFeedbackTimer],
  );

  useEffect(() => {
    if (!enabled) return;

    const handleMessage = (message: HintServerMessage) => {
      if (message.type === "low_confidence") {
        cancelPendingClear();
        if (shownRef.current?.hint.hint_id === message.hint_id) return; // duplicado
        const { type: _type, ...hint } = message;
        show({ hint, shownAt: Date.now() });
        return;
      }
      if (message.type === "hint_cleared") {
        const current = shownRef.current;
        if (!current || current.hint.hint_id !== message.hint_id) return;
        const remaining = MIN_VISIBLE_MS - (Date.now() - current.shownAt);
        cancelPendingClear();
        if (remaining <= 0) {
          show(null);
        } else {
          clearTimerRef.current = setTimeout(() => {
            clearTimerRef.current = null;
            if (shownRef.current?.hint.hint_id === message.hint_id) show(null);
          }, remaining);
        }
      }
    };

    const connection = new HintsConnection({
      fetchSocketUrl: (signal) => requestHintSocketUrl({ signal }),
      onStatus: (next) => {
        statusRef.current = next;
        setStatus(next);
        // Sin conexión no se puede saber si el aviso sigue vigente: se oculta.
        if (next !== "open") {
          cancelPendingClear();
          show(null);
        }
      },
      onMessage: handleMessage,
    });

    const onOnline = () => {
      // Al volver la red no se espera al backoff: si no hay conexión, se reinicia.
      if (statusRef.current === "open") return;
      connection.stop();
      connection.start();
    };
    window.addEventListener("online", onOnline);

    connection.start();
    return () => {
      window.removeEventListener("online", onOnline);
      connection.stop();
      cancelPendingClear();
      show(null);
    };
  }, [enabled, cancelPendingClear, show]);

  return {
    status: enabled ? status : "idle",
    hint: enabled ? (shown?.hint ?? null) : null,
    dismiss,
    feedback: enabled ? feedback : feedback === "unavailable" ? "unavailable" : "idle",
    reportFalsePositive,
  };
}
