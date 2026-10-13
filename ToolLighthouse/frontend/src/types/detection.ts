/** Contrato de respuesta de POST /detect (backend). */
export interface DetectionResult {
  /** Mejor clase del fotograma, o null si no hubo detecciones. */
  class: string | null;
  confidence: number | null;
  detections: number;
}
