/** Caja delimitadora con coordenadas normalizadas (0 a 1) respecto a la imagen enviada. */
export interface DetectionBox {
  class: string;
  confidence: number;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export type ModelKind = "custom" | "fallback";

/** Contrato de respuesta de POST /detect (backend). */
export interface DetectionResult {
  /** Mejor clase del fotograma, o null si no hubo detecciones. */
  class: string | null;
  confidence: number | null;
  detections: number;
  /** Fase 3. Ausente si el backend aún no las envía. */
  boxes?: DetectionBox[];
  /** Fase 3. Modelo que produjo la respuesta. */
  model?: ModelKind;
}
