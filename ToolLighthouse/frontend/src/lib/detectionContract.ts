import type { DetectionBox, DetectionResult, ModelKind } from "@/types/detection";

/**
 * Validación del contrato de respuesta, compartida por el proxy (servidor) y el cliente.
 * Sin dependencias de React, del DOM ni de Node.
 * Los campos de la fase 3 (boxes, model) son opcionales: si faltan o vienen mal formados
 * se omiten, pero la respuesta sigue siendo válida.
 */

const MAX_BOXES = 50;

function clamp01(value: number): number {
  return Math.min(1, Math.max(0, value));
}

function parseBox(raw: unknown): DetectionBox | null {
  if (typeof raw !== "object" || raw === null) return null;
  const { class: cls, confidence, x1, y1, x2, y2 } = raw as Record<string, unknown>;
  if (typeof cls !== "string") return null;
  const nums = [confidence, x1, y1, x2, y2];
  if (!nums.every((n) => typeof n === "number" && Number.isFinite(n))) return null;

  const box = {
    class: cls,
    confidence: clamp01(confidence as number),
    x1: clamp01(x1 as number),
    y1: clamp01(y1 as number),
    x2: clamp01(x2 as number),
    y2: clamp01(y2 as number),
  };
  return box.x2 > box.x1 && box.y2 > box.y1 ? box : null;
}

function parseBoxes(raw: unknown): DetectionBox[] | undefined {
  if (!Array.isArray(raw)) return undefined;
  return raw.slice(0, MAX_BOXES).map(parseBox).filter((b): b is DetectionBox => b !== null);
}

function parseModel(raw: unknown): ModelKind | undefined {
  return raw === "custom" || raw === "fallback" ? raw : undefined;
}

export function parseDetectionResult(data: unknown): DetectionResult | null {
  if (typeof data !== "object" || data === null) return null;
  const { class: cls, confidence, detections, boxes, model } = data as Record<string, unknown>;
  if (cls !== null && typeof cls !== "string") return null;
  if (confidence !== null && typeof confidence !== "number") return null;
  if (typeof detections !== "number") return null;

  const result: DetectionResult = { class: cls, confidence, detections };
  const parsedBoxes = parseBoxes(boxes);
  if (parsedBoxes) result.boxes = parsedBoxes;
  const parsedModel = parseModel(model);
  if (parsedModel) result.model = parsedModel;
  return result;
}
