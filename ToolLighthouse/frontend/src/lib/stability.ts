/** Filtro de estabilidad temporal (equivalente al deque(maxlen=10) de detect.py). Funciones puras, sin React. */

export interface Sample {
  class: string | null;
  confidence: number | null;
}

export interface Stability {
  /** Clase más frecuente del historial (puede no ser oficial todavía). */
  candidate: string | null;
  /** Fotogramas del historial en los que aparece `candidate`. */
  count: number;
  /** `candidate` solo si alcanza `minFrames`; si no, null (REPOSO). */
  official: string | null;
  /** Media de la confianza de los fotogramas de la clase oficial. */
  confidence: number | null;
}

/** Añade una muestra y conserva solo las últimas `size`. No modifica el arreglo original. */
export function pushSample(history: readonly Sample[], sample: Sample, size: number): Sample[] {
  return [...history, sample].slice(-size);
}

export function evaluate(history: readonly Sample[], minFrames: number): Stability {
  const counts = new Map<string, { count: number; lastIndex: number }>();
  history.forEach((sample, index) => {
    if (sample.class === null) return;
    const entry = counts.get(sample.class);
    counts.set(sample.class, { count: (entry?.count ?? 0) + 1, lastIndex: index });
  });

  let candidate: string | null = null;
  let best = { count: 0, lastIndex: -1 };
  for (const [name, entry] of counts) {
    // Ante un empate gana la clase vista más recientemente.
    if (entry.count > best.count || (entry.count === best.count && entry.lastIndex > best.lastIndex)) {
      candidate = name;
      best = entry;
    }
  }

  const official = candidate !== null && best.count >= minFrames ? candidate : null;

  let confidence: number | null = null;
  if (official !== null) {
    const values = history
      .filter((s) => s.class === official && s.confidence !== null)
      .map((s) => s.confidence as number);
    if (values.length > 0) confidence = values.reduce((a, b) => a + b, 0) / values.length;
  }

  return { candidate, count: best.count, official, confidence };
}
