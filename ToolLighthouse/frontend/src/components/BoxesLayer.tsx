"use client";

import { useEffect, useRef, useState } from "react";
import { formatClass } from "@/lib/formatClass";
import type { DetectionBox } from "@/types/detection";

interface Props {
  videoRef: React.RefObject<HTMLVideoElement | null>;
  boxes: DetectionBox[];
}

const COLOR = "#64dc32";

/**
 * <canvas> sobre el video. El video usa `object-fit: cover`, así que las coordenadas
 * normalizadas se proyectan sobre la imagen ya escalada y recortada, no sobre el contenedor.
 */
export function BoxesLayer({ videoRef, boxes }: Props) {
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [size, setSize] = useState({ width: 0, height: 0 });

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const update = () => setSize({ width: canvas.clientWidth, height: canvas.clientHeight });
    update();
    const observer = new ResizeObserver(update);
    observer.observe(canvas);
    return () => observer.disconnect();
  }, []);

  useEffect(() => {
    const canvas = canvasRef.current;
    const video = videoRef.current;
    const context = canvas?.getContext("2d");
    if (!canvas || !context) return;

    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(size.width * dpr);
    canvas.height = Math.round(size.height * dpr);
    context.setTransform(dpr, 0, 0, dpr, 0, 0);
    context.clearRect(0, 0, size.width, size.height);

    if (!video || video.videoWidth === 0 || video.videoHeight === 0 || boxes.length === 0) return;

    // Misma geometría que object-fit: cover.
    const scale = Math.max(size.width / video.videoWidth, size.height / video.videoHeight);
    const drawnWidth = video.videoWidth * scale;
    const drawnHeight = video.videoHeight * scale;
    const offsetX = (size.width - drawnWidth) / 2;
    const offsetY = (size.height - drawnHeight) / 2;

    context.lineWidth = 2;
    context.font = "600 12px ui-sans-serif, system-ui, sans-serif";
    context.textBaseline = "middle";

    for (const box of boxes) {
      const x = offsetX + box.x1 * drawnWidth;
      const y = offsetY + box.y1 * drawnHeight;
      const w = (box.x2 - box.x1) * drawnWidth;
      const h = (box.y2 - box.y1) * drawnHeight;

      context.strokeStyle = COLOR;
      context.strokeRect(x, y, w, h);

      const label = `${formatClass(box.class)} ${Math.round(box.confidence * 100)} %`;
      const labelHeight = 18;
      const labelWidth = context.measureText(label).width + 10;
      const labelY = y - labelHeight >= 0 ? y - labelHeight : y;
      context.fillStyle = COLOR;
      context.fillRect(x, labelY, labelWidth, labelHeight);
      context.fillStyle = "#0b1a05";
      context.fillText(label, x + 5, labelY + labelHeight / 2);
    }
  }, [boxes, size, videoRef]);

  return <canvas ref={canvasRef} className="boxes" aria-hidden="true" />;
}
