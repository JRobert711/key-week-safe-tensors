"""
ToolLighthouse - Fine-Tuning Pipeline (YOLOv8 Large)
Entrena el detector de herramientas y equipos de laboratorio usando Ultralytics YOLOv8.
Exporta automaticamente el mejor checkpoint (best.pt) a formato ONNX al culminar.
"""

import sys
import shutil
import logging
import argparse
from pathlib import Path

# Configuracion de logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("TrainPipeline")


def parse_arguments() -> argparse.Namespace:
    """Configuracion de parametros de entrenamiento CLI con valores oficiales del proyecto."""
    parser = argparse.ArgumentParser(
        description="ToolLighthouse - Fine-Tuning de YOLOv8 Large para inventario de laboratorio"
    )
    parser.add_argument(
        "--weights",
        type=str,
        default="yolov8l.pt",
        help="Checkpoint base de YOLOv8 (por defecto: yolov8l.pt)"
    )
    parser.add_argument(
        "--data",
        type=str,
        default=None,
        help="Ruta al archivo data.yaml (por defecto busca dataset/data.yaml)"
    )
    parser.add_argument(
        "--epochs",
        type=int,
        default=60,
        help="Numero maximo de epocas de entrenamiento (oficial: 60)"
    )
    parser.add_argument(
        "--batch",
        type=int,
        default=4,
        help="Batch size (oficial: 4 para prevenir OOM en GPU/RAM local con modelo Large)"
    )
    parser.add_argument(
        "--imgsz",
        type=int,
        default=640,
        help="Resolucion espacial de entrada en pixeles (oficial: 640)"
    )
    parser.add_argument(
        "--patience",
        type=int,
        default=15,
        help="Epocas sin mejora antes de disparar Early Stopping (oficial: 15)"
    )
    parser.add_argument(
        "--optimizer",
        type=str,
        default="AdamW",
        help="Optimizador de gradiente descendente (oficial: AdamW)"
    )
    parser.add_argument(
        "--device",
        type=str,
        default="",
        help="Dispositivo de computo ('0' para GPU NVIDIA, 'cpu' para CPU, '' para autoseleccion)"
    )
    return parser.parse_args()


def validate_dataset_presence(data_yaml_path: Path, dataset_root: Path) -> bool:
    """
    Verifica que el archivo de configuracion y las carpetas de imagenes contengan datos
    antes de lanzar el entrenamiento para evitar excepciones de Ultralytics.
    """
    if not data_yaml_path.exists():
        logger.error(f"No se encontro el archivo de configuracion: {data_yaml_path}")
        return False

    train_imgs_dir = dataset_root / "images" / "train"
    val_imgs_dir = dataset_root / "images" / "val"

    valid_extensions = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    train_files = [f for f in train_imgs_dir.glob("*") if f.suffix.lower() in valid_extensions] if train_imgs_dir.exists() else []
    val_files = [f for f in val_imgs_dir.glob("*") if f.suffix.lower() in valid_extensions] if val_imgs_dir.exists() else []

    logger.info(f"Imagenes detectadas -> Train: {len(train_files)} | Val: {len(val_files)}")

    if len(train_files) == 0:
        logger.warning(
            "[AVISO] La carpeta 'dataset/images/train' esta vacia.\n"
            "Asegurate de exportar tu dataset desde Label Studio en formato YOLO y\n"
            "descomprimirlo dentro de 'ToolLighthouse/dataset/' antes de entrenar."
        )
        return False

    return True


def run_training() -> None:
    """Ejecuta el ciclo de fine-tuning y la exportacion a ONNX."""
    args = parse_arguments()

    # Resolucion de rutas relativas al proyecto
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent
    dataset_dir = project_root / "dataset"
    models_dir = project_root / "models"
    runs_dir = project_root / "runs"

    models_dir.mkdir(parents=True, exist_ok=True)

    data_yaml_path = Path(args.data) if args.data else dataset_dir / "data.yaml"

    logger.info("=" * 60)
    logger.info("        INICIANDO PIPELINE DE ENTRENAMIENTO - TOOLLIGHTHOUSE")
    logger.info("=" * 60)
    logger.info(f"Directorio raiz del proyecto: {project_root}")
    logger.info(f"Modelo base:                  {args.weights}")
    logger.info(f"Configuracion dataset:        {data_yaml_path}")
    logger.info(f"Epocas / Batch / ImgSz:       {args.epochs} / {args.batch} / {args.imgsz}")
    logger.info(f"Patience / Optimizer:         {args.patience} / {args.optimizer}")
    logger.info("-" * 60)

    # Validar presencia del dataset
    if not validate_dataset_presence(data_yaml_path, dataset_dir):
        logger.error("Entrenamiento abortado: Dataset incompleto o no encontrado.")
        sys.exit(1)

    # Importar Ultralytics
    try:
        from ultralytics import YOLO
    except ImportError:
        logger.error(
            "La libreria 'ultralytics' no esta instalada. "
            "Ejecuta: pip install -r requirements.txt"
        )
        sys.exit(1)

    # Cargar modelo base YOLOv8 Large
    logger.info(f"Cargando arquitectura base '{args.weights}'...")
    model = YOLO(args.weights)

    # Iniciar entrenamiento con parametros oficiales
    training_kwargs = {
        "data": str(data_yaml_path),
        "epochs": args.epochs,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "patience": args.patience,
        "optimizer": args.optimizer,
        "project": str(runs_dir / "detect"),
        "name": "train",
        "exist_ok": True,
        "verbose": True
    }
    if args.device:
        training_kwargs["device"] = args.device

    logger.info("Iniciando fine-tuning...")
    model.train(**training_kwargs)

    # Localizar mejor checkpoint
    best_weights_path = runs_dir / "detect" / "train" / "weights" / "best.pt"
    if not best_weights_path.exists():
        # Fallback al atributo del trainer si la ruta relativa difiere
        if hasattr(model, "trainer") and hasattr(model.trainer, "best"):
            best_weights_path = Path(model.trainer.best)

    if not best_weights_path.exists():
        logger.error(f"No se localizo el archivo de pesos 'best.pt' en {best_weights_path}.")
        return

    logger.info("=" * 60)
    logger.info(f"Entrenamiento completado exitosamente.")
    logger.info(f"Pesos optimos guardados en: {best_weights_path}")

    # Copiar a carpeta models/ para persistencia rapida
    target_best_pt = models_dir / "best.pt"
    try:
        shutil.copy2(best_weights_path, target_best_pt)
        logger.info(f"Copia de conveniencia guardada en: {target_best_pt}")
    except Exception as exc:
        logger.warning(f"No se pudo copiar 'best.pt' a models/: {exc}")

    # Exportar automaticamente a ONNX para despliegue de alta eficiencia
    logger.info("-" * 60)
    logger.info("Exportando el modelo optimizado a formato ONNX...")
    try:
        export_model = YOLO(str(best_weights_path))
        onnx_file_path = export_model.export(format="onnx")
        logger.info(f"Exportacion a ONNX completada exitosamente: {onnx_file_path}")

        # Copiar ONNX a carpeta models/
        if onnx_file_path and Path(onnx_file_path).exists():
            target_best_onnx = models_dir / "best.onnx"
            shutil.copy2(onnx_file_path, target_best_onnx)
            logger.info(f"Copia ONNX guardada en: {target_best_onnx}")
    except Exception as exc:
        logger.error(f"Fallo durante la exportacion a ONNX: {exc}")

    logger.info("=" * 60)
    logger.info("Proceso concluido. El modelo esta listo para ejecucion en src/detect.py")
    logger.info("=" * 60)


if __name__ == "__main__":
    run_training()
