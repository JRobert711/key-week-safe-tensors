"""
ToolLighthouse - Real-Time Vision & Hardware Bridge
Captura video en vivo, ejecuta inferencia con YOLOv8, aplica filtro de persistencia temporal
(anti-parpadeo) y despacha eventos UART/MOCK hacia el microcontrolador para iluminacion 3D.
"""

import sys
import time
import logging
import argparse
from pathlib import Path
from collections import deque, Counter
from typing import Optional, Tuple

import cv2

# Importar el controlador serie del proyecto
try:
    from serial_bridge import SerialController
except ImportError:
    from src.serial_bridge import SerialController

# Configuracion de logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("DetectBridge")

# Mapeo oficial de las 9 clases del inventario de laboratorio
PROJECT_CLASSES = {
    0: "bascula",
    1: "caja_sensores",
    2: "capacitor_variable",
    3: "cautin",
    4: "estacion_soldadura",
    5: "fuente_voltaje",
    6: "multimetro_analogico",
    7: "multimetro_digital",
    8: "ventilador_pasco",
}


def parse_arguments() -> argparse.Namespace:
    """Configura los argumentos por linea de comandos."""
    parser = argparse.ArgumentParser(
        description="ToolLighthouse - Deteccion en tiempo real con integracion serie"
    )
    parser.add_argument(
        "--camera",
        type=int,
        default=0,
        help="Indice de la camara web (por defecto: 0)"
    )
    parser.add_argument(
        "--weights",
        type=str,
        default=None,
        help="Ruta especifica al archivo de pesos (.pt). Si no se indica, busca best.pt o fallback a yolov8l.pt"
    )
    parser.add_argument(
        "--conf",
        type=float,
        default=0.70,
        help="Umbral estricto de confianza para detecciones (oficial: 0.70)"
    )
    parser.add_argument(
        "--port",
        type=str,
        default="COM3",
        help="Puerto serie UART para Arduino/ESP32 (por defecto: COM3)"
    )
    parser.add_argument(
        "--baudrate",
        type=int,
        default=115200,
        help="Velocidad en baudios del puerto serie (por defecto: 115200)"
    )
    parser.add_argument(
        "--mock",
        action="store_true",
        help="Forzar modo MOCK de simulacion serial sin intentar abrir puerto fisico"
    )
    return parser.parse_args()


def resolve_model_weights(requested_path: Optional[str]) -> Tuple[str, bool]:
    """
    Determina la ruta de pesos a cargar segun la jerarquia del proyecto:
    1. Argumento explicito (--weights)
    2. Checkpoint optimizado runs/detect/train/weights/best.pt
    3. Copia en models/best.pt
    4. Fallback temporal: yolov8l.pt

    :return: (ruta_pesos, es_fallback)
    """
    if requested_path:
        req = Path(requested_path)
        if req.exists():
            return str(req), False
        logger.warning(f"Ruta de pesos solicitada '{requested_path}' no existe. Buscando alternativas...")

    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent

    trained_weights = project_root / "runs" / "detect" / "train" / "weights" / "best.pt"
    models_best = project_root / "models" / "best.pt"

    if trained_weights.exists():
        logger.info(f"Cargando modelo entrenado oficial: {trained_weights}")
        return str(trained_weights), False

    if models_best.exists():
        logger.info(f"Cargando modelo exportado: {models_best}")
        return str(models_best), False

    logger.warning("=" * 60)
    logger.warning("[MODO FALLBACK ACTIVO]")
    logger.warning("No se encontro un modelo entrenado ('runs/.../best.pt' o 'models/best.pt').")
    logger.warning("Cargando temporalmente 'yolov8l.pt' como fallback para pruebas de integracion.")
    logger.warning("Entrena tu detector ejecutando: python src/train.py")
    logger.warning("=" * 60)
    return "yolov8l.pt", True


def draw_hud(
    frame,
    fps: float,
    official_class: Optional[int],
    stability_count: int,
    history_len: int,
    serial_mode_str: str,
    last_byte_str: str,
    is_fallback: bool
):
    """
    Dibuja una interfaz visual limpia (HUD) con telemetria en tiempo real:
    - Estado de deteccion oficial
    - Filtro de persistencia (X/10)
    - Mensaje UART transmitido y estado del enlace
    - FPS de inferencia
    """
    overlay = frame.copy()
    h, w, _ = frame.shape

    # Panel superior semitransparente para estadisticas
    panel_w = 420
    panel_h = 160
    cv2.rectangle(overlay, (10, 10), (10 + panel_w, 10 + panel_h), (20, 24, 30), -1)
    cv2.addWeighted(overlay, 0.75, frame, 0.25, 0, frame)

    # Borde estetico del panel
    cv2.rectangle(frame, (10, 10), (10 + panel_w, 10 + panel_h), (60, 80, 100), 1)

    # Titulo de la aplicacion
    cv2.putText(
        frame,
        "ToolLighthouse | Detector 3D",
        (25, 35),
        cv2.FONT_HERSHEY_DUPLEX,
        0.65,
        (0, 215, 255),
        1,
        cv2.LINE_AA
    )

    # Nombre de la herramienta oficial detectada
    if official_class is not None:
        name = PROJECT_CLASSES.get(official_class, f"Clase ID {official_class}")
        status_text = f"ACTIVO: {name.upper()}"
        status_color = (50, 220, 100)  # Verde brillante
    else:
        status_text = "ESTADO: REPOSO (Ninguno)"
        status_color = (180, 180, 180)  # Gris

    cv2.putText(
        frame,
        status_text,
        (25, 65),
        cv2.FONT_HERSHEY_DUPLEX,
        0.58,
        status_color,
        1,
        cv2.LINE_AA
    )

    # Metricas de persistencia temporal
    persist_text = f"Estabilidad temporal: {stability_count}/{history_len} frames (min 7)"
    cv2.putText(
        frame,
        persist_text,
        (25, 92),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.48,
        (220, 220, 220),
        1,
        cv2.LINE_AA
    )

    # Estado del canal serie
    serial_text = f"UART: {serial_mode_str} | Byte: {last_byte_str}"
    cv2.putText(
        frame,
        serial_text,
        (25, 118),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.48,
        (100, 200, 255),
        1,
        cv2.LINE_AA
    )

    # FPS y modo de modelo
    model_tag = "FALLBACK (yolov8l.pt)" if is_fallback else "CUSTOM (best.pt)"
    perf_text = f"FPS: {fps:4.1f} | Modelo: {model_tag}"
    cv2.putText(
        frame,
        perf_text,
        (25, 145),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.45,
        (200, 160, 80) if is_fallback else (140, 220, 140),
        1,
        cv2.LINE_AA
    )

    # Indicador de salida
    cv2.putText(
        frame,
        "Presiona 'q' para salir",
        (w - 210, h - 15),
        cv2.FONT_HERSHEY_SIMPLEX,
        0.50,
        (0, 0, 255),
        1,
        cv2.LINE_AA
    )


def _last_byte_text(serial_ctrl: SerialController) -> str:
    """Ultimo mensaje enviado al microcontrolador, como texto."""
    if serial_ctrl.last_sent_byte:
        return serial_ctrl.last_sent_byte.decode("ascii", errors="replace")
    return "Ninguno"


def main() -> None:
    """Bucle principal de adquisicion, inferencia y control de hardware."""
    args = parse_arguments()

    # Importar Ultralytics
    try:
        from ultralytics import YOLO
    except ImportError:
        logger.error("Ultralytics no esta instalado. Ejecuta: pip install -r requirements.txt")
        sys.exit(1)

    # Resolver pesos del modelo
    weights_path, is_fallback = resolve_model_weights(args.weights)
    logger.info(f"Cargando modelo YOLO desde: {weights_path}")
    model = YOLO(weights_path)

    # Inicializar controlador serie
    serial_ctrl = SerialController(
        port=args.port,
        baudrate=args.baudrate,
        force_mock=args.mock
    )

    # Iniciar captura de video
    logger.info(f"Abriendo camara indice {args.camera}...")
    cap = cv2.VideoCapture(args.camera)

    if not cap.isOpened():
        logger.error(f"No fue posible abrir la camara con indice {args.camera}.")
        serial_ctrl.close()
        sys.exit(1)

    # Configurar resolucion estandar de captura (720p si soportado)
    cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
    cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    # Filtro de persistencia temporal (cola FIFO de longitud fija 10)
    history_window = deque(maxlen=10)
    min_required_frames = 7

    # Variables de telemetria
    prev_time = time.time()
    fps = 0.0

    # Ultima clase que estuvo activa de forma estable (para evento de desaparicion)
    last_active_class: Optional[int] = None

    logger.info("=" * 60)
    logger.info("  SISTEMA TOOLLIGHTHOUSE ACTIVO EN TIEMPO REAL")
    logger.info("  - Umbral de confianza:  >= {:.2f}".format(args.conf))
    logger.info("  - Filtro persistencia:  >= 7 de 10 fotogramas")
    logger.info("  - Modo Serial:          {}".format("MOCK" if serial_ctrl.mock_mode else args.port))
    logger.info("  - Presiona 'q' en la ventana para finalizar")
    logger.info("=" * 60)

    try:
        while True:
            ret, frame = cap.read()
            if not ret:
                logger.warning("Fotograma de camara no recibido. Reintentando...")
                time.sleep(0.05)
                continue

            current_time = time.time()
            dt = current_time - prev_time
            prev_time = current_time
            if dt > 0:
                fps = (0.9 * fps) + (0.1 * (1.0 / dt)) if fps > 0 else (1.0 / dt)

            # Inferencia con YOLOv8
            results = model(frame, conf=args.conf, verbose=False)

            # Extraer la deteccion mas relevante en el frame actual
            current_frame_class: Optional[int] = None
            boxes = results[0].boxes

            if boxes is not None and len(boxes) > 0:
                # Tomar la deteccion con mayor confianza
                best_box = max(boxes, key=lambda b: float(b.conf[0]))
                current_frame_class = int(best_box.cls[0])

            # Actualizar historial temporal
            history_window.append(current_frame_class)

            # Persistencia temporal (anti-parpadeo):
            # una clase es oficial unicamente si aparece >= 7 de los ultimos 10 frames
            counts = Counter(history_window)
            official_class: Optional[int] = None
            stability_count = 0

            for cls_id, count in counts.items():
                if cls_id is not None and count >= min_required_frames:
                    official_class = cls_id
                    stability_count = count
                    break

            # Si ninguna clase cumple el umbral, el estado oficial es reposo (None)
            if official_class is None:
                stability_count = counts.get(None, 0)

            # Despachar eventos al microcontrolador (solo cuando el estado cambia)
            if official_class is not None:
                # Cambio directo A -> B: programar el apagado de A antes de encender B
                if last_active_class is not None and last_active_class != official_class:
                    if serial_ctrl.send_disappearance_event(last_active_class):
                        prev_desc = PROJECT_CLASSES.get(last_active_class, f"Clase {last_active_class}")
                        logger.info(
                            f"[DESAPARICION ESTABLE] Herramienta: {prev_desc} -> Byte: '{_last_byte_text(serial_ctrl)}'"
                        )
                    last_active_class = None

                # --- APARICION ESTABLE ---
                if serial_ctrl.send_appearance_event(official_class):
                    last_active_class = official_class
                    cls_desc = PROJECT_CLASSES.get(official_class, f"Clase {official_class}")
                    logger.info(
                        f"[APARICION ESTABLE]  Herramienta: {cls_desc} -> Byte: '{_last_byte_text(serial_ctrl)}'"
                    )
            else:
                # --- DESAPARICION ESTABLE ---
                if last_active_class is not None:
                    if serial_ctrl.send_disappearance_event(last_active_class):
                        cls_desc = PROJECT_CLASSES.get(last_active_class, f"Clase {last_active_class}")
                        logger.info(
                            f"[DESAPARICION ESTABLE] Herramienta: {cls_desc} -> Byte: '{_last_byte_text(serial_ctrl)}'"
                        )
                        last_active_class = None

            # Frame visual con cajas delimitadoras dibujadas por YOLO
            annotated_frame = results[0].plot()

            serial_mode_label = "MOCK (Simulador)" if serial_ctrl.mock_mode else f"UART ({serial_ctrl.port})"

            # Renderizar interfaz de usuario (HUD)
            draw_hud(
                frame=annotated_frame,
                fps=fps,
                official_class=official_class,
                stability_count=stability_count,
                history_len=len(history_window),
                serial_mode_str=serial_mode_label,
                last_byte_str=_last_byte_text(serial_ctrl),
                is_fallback=is_fallback
            )

            # Visualizacion en ventana OpenCV
            cv2.imshow("ToolLighthouse - Detector en Tiempo Real", annotated_frame)

            # Salida limpia al pulsar 'q' o ESC (27)
            key = cv2.waitKey(1) & 0xFF
            if key == ord('q') or key == 27:
                logger.info("Peticion de terminacion recibida por teclado.")
                break

    except KeyboardInterrupt:
        logger.info("Interrupcion por teclado (Ctrl+C).")

    finally:
        logger.info("Liberando recursos y apagando maqueta...")
        cap.release()
        cv2.destroyAllWindows()
        serial_ctrl.close()
        logger.info("Sistema ToolLighthouse finalizado limpiamente.")


if __name__ == "__main__":
    main()
