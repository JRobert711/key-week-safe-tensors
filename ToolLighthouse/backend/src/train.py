"""
ToolLighthouse - Fine-Tuning Pipeline (YOLOv8 Large)
Entrena el detector de herramientas y equipos de laboratorio usando Ultralytics YOLOv8.

Incluye:
  - Funciona con dataset plano (images/ + labels/ + classes.txt): no requiere data.yaml
  - Split reproducible train / val / test (sin mover ni copiar imagenes: usa listas .txt)
  - Data augmentation configurable por CLI
  - Evaluacion final sobre el conjunto de test
  - Exportacion automatica del mejor checkpoint (best.pt) a ONNX
"""

import sys
import shutil
import random
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

VALID_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


# ----------------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------------
def parse_arguments() -> argparse.Namespace:
    """Configuracion de parametros de entrenamiento CLI con valores oficiales del proyecto."""
    parser = argparse.ArgumentParser(
        description="ToolLighthouse - Fine-Tuning de YOLOv8 Large para inventario de laboratorio"
    )

    # --- Entrenamiento base ---
    parser.add_argument("--weights", type=str, default="yolov8l.pt",
                        help="Checkpoint base de YOLOv8 (por defecto: yolov8l.pt)")
    parser.add_argument("--data", type=str, default=None,
                        help="Ruta a un data.yaml (opcional). Si no existe, se usan los nombres de dataset/classes.txt")
    parser.add_argument("--epochs", type=int, default=60,
                        help="Numero maximo de epocas de entrenamiento (oficial: 60)")
    parser.add_argument("--batch", type=int, default=4,
                        help="Batch size (oficial: 4 para prevenir OOM en GPU/RAM local con modelo Large)")
    parser.add_argument("--imgsz", type=int, default=640,
                        help="Resolucion espacial de entrada en pixeles (oficial: 640)")
    parser.add_argument("--patience", type=int, default=15,
                        help="Epocas sin mejora antes de disparar Early Stopping (oficial: 15)")
    parser.add_argument("--optimizer", type=str, default="AdamW",
                        help="Optimizador de gradiente descendente (oficial: AdamW)")
    parser.add_argument("--device", type=str, default="",
                        help="Dispositivo ('0' para GPU NVIDIA, 'cpu' para CPU, '' para autoseleccion)")
    parser.add_argument("--seed", type=int, default=42,
                        help="Semilla para reproducibilidad (split y entrenamiento)")

    # --- Split de datos ---
    split = parser.add_argument_group("Split de datos")
    split.add_argument("--val-split", type=float, default=0.2,
                       help="Fraccion del dataset para validacion (por defecto: 0.2)")
    split.add_argument("--test-split", type=float, default=0.1,
                       help="Fraccion del dataset para test (por defecto: 0.1)")
    split.add_argument("--no-split", action="store_true",
                       help="Desactiva el re-split y usa data.yaml tal cual (sin evaluacion en test)")

    # --- Data augmentation ---
    aug = parser.add_argument_group("Data augmentation (Ultralytics)")
    aug.add_argument("--no-augment", action="store_true",
                     help="Desactiva todo el augmentation (pone los valores en 0)")
    aug.add_argument("--hsv-h", type=float, default=0.015, help="Variacion de tono (0-1)")
    aug.add_argument("--hsv-s", type=float, default=0.7, help="Variacion de saturacion (0-1)")
    aug.add_argument("--hsv-v", type=float, default=0.4, help="Variacion de brillo (0-1)")
    aug.add_argument("--degrees", type=float, default=10.0, help="Rotacion aleatoria en grados")
    aug.add_argument("--translate", type=float, default=0.1, help="Traslacion (fraccion de imagen)")
    aug.add_argument("--scale", type=float, default=0.5, help="Escala aleatoria (+/- fraccion)")
    aug.add_argument("--shear", type=float, default=2.0, help="Cizallamiento en grados")
    aug.add_argument("--perspective", type=float, default=0.0, help="Perspectiva (0-0.001)")
    aug.add_argument("--fliplr", type=float, default=0.5, help="Probabilidad de flip horizontal")
    aug.add_argument("--flipud", type=float, default=0.0, help="Probabilidad de flip vertical")
    aug.add_argument("--mosaic", type=float, default=1.0, help="Probabilidad de mosaic")
    aug.add_argument("--mixup", type=float, default=0.1, help="Probabilidad de mixup")
    aug.add_argument("--copy-paste", type=float, default=0.0, help="Probabilidad de copy-paste (segmentacion)")
    aug.add_argument("--erasing", type=float, default=0.4, help="Probabilidad de random erasing")
    aug.add_argument("--close-mosaic", type=int, default=10,
                     help="Desactiva mosaic en las ultimas N epocas para estabilizar")

    return parser.parse_args()


def build_augmentation_kwargs(args: argparse.Namespace) -> dict:
    """Construye el diccionario de augmentation para model.train()."""
    keys = {
        "hsv_h": args.hsv_h,
        "hsv_s": args.hsv_s,
        "hsv_v": args.hsv_v,
        "degrees": args.degrees,
        "translate": args.translate,
        "scale": args.scale,
        "shear": args.shear,
        "perspective": args.perspective,
        "flipud": args.flipud,
        "fliplr": args.fliplr,
        "mosaic": args.mosaic,
        "mixup": args.mixup,
        "copy_paste": args.copy_paste,
        "erasing": args.erasing,
        "close_mosaic": args.close_mosaic,
    }
    if args.no_augment:
        keys = {k: 0 for k in keys}
        # Ultralytics aplica auto_augment/erasing en clasificacion; aqui basta con ceros.
    return keys


# ----------------------------------------------------------------------------
# Dataset
# ----------------------------------------------------------------------------
def list_images(folder: Path) -> list:
    """Lista las imagenes validas de una carpeta (no recursivo)."""
    if not folder.exists():
        return []
    return sorted(f for f in folder.glob("*") if f.suffix.lower() in VALID_EXTENSIONS)


def collect_images(dataset_root: Path) -> list:
    """
    Reune las imagenes del dataset sin duplicados. Soporta dos estructuras:
      - Plana:    dataset/images/*.jpg  (+ dataset/labels/*.txt)
      - Dividida: dataset/images/{train,val,test}/*.jpg
    """
    images_dir = dataset_root / "images"
    folders = [images_dir] + [images_dir / s for s in ("train", "val", "test")]
    pool, seen = [], set()
    for folder in folders:
        for img in list_images(folder):
            resolved = img.resolve()
            if resolved not in seen:
                seen.add(resolved)
                pool.append(resolved)
    return pool


def label_path_for(img: Path) -> Path:
    """Ruta de la etiqueta de una imagen (misma regla que Ultralytics: images -> labels)."""
    parts = list(img.parts)
    for i in range(len(parts) - 1, -1, -1):
        if parts[i] == "images":
            parts[i] = "labels"
            break
    return Path(*parts).with_suffix(".txt")


def load_class_names(dataset_root: Path, data_yaml_path: Path) -> dict:
    """
    Obtiene los nombres de clases, en este orden:
      1. 'names' del data.yaml (si existe)
      2. dataset/classes.txt (una clase por linea; el orden define el id 0, 1, 2...)
    """
    import yaml

    if data_yaml_path is not None and data_yaml_path.exists():
        with open(data_yaml_path, "r", encoding="utf-8") as fh:
            cfg = yaml.safe_load(fh) or {}
        if cfg.get("names"):
            names = cfg["names"]
            return names if isinstance(names, dict) else {i: n for i, n in enumerate(names)}

    classes_txt = dataset_root / "classes.txt"
    if classes_txt.exists():
        lines = [l.strip() for l in classes_txt.read_text(encoding="utf-8").splitlines()]
        names = [l for l in lines if l]
        if names:
            return {i: n for i, n in enumerate(names)}

    raise ValueError(
        "No se encontraron nombres de clases: falta 'names' en el data.yaml "
        f"y tampoco existe {classes_txt}."
    )


def validate_dataset_presence(dataset_root: Path) -> bool:
    """Verifica que existan imagenes y etiquetas antes de lanzar el entrenamiento."""
    pool = collect_images(dataset_root)
    logger.info(f"Imagenes detectadas en '{dataset_root / 'images'}': {len(pool)}")

    if not pool:
        logger.warning(
            "[AVISO] No se encontraron imagenes en 'dataset/images/'.\n"
            "Asegurate de exportar tu dataset desde Label Studio en formato YOLO y\n"
            "descomprimirlo dentro de la carpeta 'dataset/' antes de entrenar."
        )
        return False

    if not (dataset_root / "labels").exists():
        logger.error(f"No existe la carpeta de etiquetas: {dataset_root / 'labels'}")
        return False

    return True


def create_train_val_test_split(
    data_yaml_path: Path,
    dataset_root: Path,
    val_ratio: float,
    test_ratio: float,
    seed: int,
) -> Path:
    """
    Junta todas las imagenes disponibles, las mezcla con una semilla fija y genera
    listas train.txt / val.txt / test.txt junto con un YAML nuevo (data_split.yaml).

    No mueve ni copia archivos: Ultralytics lee las listas .txt y busca las etiquetas
    reemplazando '/images/' por '/labels/' en cada ruta (estructura estandar YOLO).

    Returns:
        Ruta del nuevo YAML (dataset/split/data_split.yaml).
    """
    import yaml

    if not (0 <= val_ratio < 1 and 0 <= test_ratio < 1 and val_ratio + test_ratio < 1):
        raise ValueError("val-split y test-split deben estar en [0, 1) y sumar menos de 1.")

    pool = collect_images(dataset_root)
    n = len(pool)
    if n < 3:
        raise ValueError(f"Se necesitan al menos 3 imagenes para dividir en train/val/test (hay {n}).")

    names = load_class_names(dataset_root, data_yaml_path)
    nc = len(names)

    # Revisar etiquetas: faltantes (fondo) e ids de clase fuera de rango
    missing_labels = 0
    class_counts = {i: 0 for i in range(nc)}
    for img in pool:
        label_file = label_path_for(img)
        if not label_file.exists():
            missing_labels += 1
            continue
        for line in label_file.read_text(encoding="utf-8").splitlines():
            tokens = line.split()
            if not tokens:
                continue
            cls_id = int(float(tokens[0]))
            if cls_id >= nc or cls_id < 0:
                raise ValueError(
                    f"{label_file} usa la clase {cls_id}, pero solo hay {nc} clases definidas."
                )
            class_counts[cls_id] += 1

    if missing_labels:
        logger.warning(f"{missing_labels} imagenes no tienen archivo de etiquetas (se tratan como fondo).")
    logger.info("Instancias por clase: " + ", ".join(f"{names[i]}={c}" for i, c in class_counts.items()))
    empty = [names[i] for i, c in class_counts.items() if c == 0]
    if empty:
        logger.warning(f"Clases sin ninguna instancia etiquetada: {', '.join(empty)}")

    # Mezcla reproducible
    rng = random.Random(seed)
    rng.shuffle(pool)

    n_test = max(1, round(n * test_ratio)) if test_ratio > 0 else 0
    n_val = max(1, round(n * val_ratio)) if val_ratio > 0 else 0
    n_train = n - n_val - n_test
    if n_train < 1:
        raise ValueError("Dataset demasiado pequeno para esos porcentajes de val/test.")

    test_files = pool[:n_test]
    val_files = pool[n_test:n_test + n_val]
    train_files = pool[n_test + n_val:]

    # Escribir listas
    split_dir = dataset_root / "split"
    split_dir.mkdir(parents=True, exist_ok=True)
    for name, files in (("train", train_files), ("val", val_files), ("test", test_files)):
        (split_dir / f"{name}.txt").write_text(
            "\n".join(str(f) for f in files) + ("\n" if files else ""), encoding="utf-8"
        )

    new_cfg = {
        "path": str(dataset_root.resolve()),
        "train": str((split_dir / "train.txt").resolve()),
        "val": str((split_dir / "val.txt").resolve()),
        "names": names,
    }
    if test_files:
        new_cfg["test"] = str((split_dir / "test.txt").resolve())

    new_yaml_path = split_dir / "data_split.yaml"
    with open(new_yaml_path, "w", encoding="utf-8") as fh:
        yaml.safe_dump(new_cfg, fh, sort_keys=False, allow_unicode=True)

    logger.info(
        f"Split generado (seed={seed}) -> Train: {len(train_files)} | "
        f"Val: {len(val_files)} | Test: {len(test_files)}"
    )
    logger.info(f"YAML del split (generado automaticamente): {new_yaml_path}")
    return new_yaml_path


# ----------------------------------------------------------------------------
# Pipeline principal
# ----------------------------------------------------------------------------
def run_training() -> None:
    """Ejecuta el split, el fine-tuning, la evaluacion en test y la exportacion a ONNX."""
    args = parse_arguments()

    # Resolucion de rutas relativas al proyecto
    script_dir = Path(__file__).resolve().parent
    project_root = script_dir.parent
    dataset_dir = project_root / "dataset"
    models_dir = project_root / "models"
    runs_dir = project_root / "runs"

    models_dir.mkdir(parents=True, exist_ok=True)

    data_yaml_path = Path(args.data) if args.data else dataset_dir / "data.yaml"
    augment_kwargs = build_augmentation_kwargs(args)

    logger.info("=" * 60)
    logger.info("        INICIANDO PIPELINE DE ENTRENAMIENTO - TOOLLIGHTHOUSE")
    logger.info("=" * 60)
    logger.info(f"Directorio raiz del proyecto: {project_root}")
    logger.info(f"Modelo base:                  {args.weights}")
    logger.info(f"Dataset:                      {dataset_dir}")
    logger.info(f"data.yaml:                    {data_yaml_path if data_yaml_path.exists() else '(no existe: se usa classes.txt)'}")
    logger.info(f"Epocas / Batch / ImgSz:       {args.epochs} / {args.batch} / {args.imgsz}")
    logger.info(f"Patience / Optimizer:         {args.patience} / {args.optimizer}")
    logger.info(f"Split val / test:             {args.val_split} / {args.test_split}"
                + (" (desactivado)" if args.no_split else ""))
    logger.info(f"Augmentation:                 {'DESACTIVADO' if args.no_augment else 'activo'}")
    logger.info("-" * 60)

    # Validar presencia del dataset
    if not validate_dataset_presence(dataset_dir):
        logger.error("Entrenamiento abortado: Dataset incompleto o no encontrado.")
        sys.exit(1)

    # Crear split train/val/test
    has_test = False
    if args.no_split:
        if not data_yaml_path.exists():
            logger.error(
                f"Con --no-split se necesita un data.yaml existente ({data_yaml_path}). "
                "Quita --no-split para que se genere automaticamente."
            )
            sys.exit(1)
        training_data_yaml = data_yaml_path
    else:
        try:
            training_data_yaml = create_train_val_test_split(
                data_yaml_path, dataset_dir, args.val_split, args.test_split, args.seed
            )
            has_test = args.test_split > 0
        except Exception as exc:
            logger.error(f"No se pudo crear el split del dataset: {exc}")
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

    # Parametros de entrenamiento (incluye augmentation)
    training_kwargs = {
        "data": str(training_data_yaml),
        "epochs": args.epochs,
        "imgsz": args.imgsz,
        "batch": args.batch,
        "patience": args.patience,
        "optimizer": args.optimizer,
        "seed": args.seed,
        "project": str(runs_dir / "detect"),
        "name": "train",
        "exist_ok": True,
        "verbose": True,
        **augment_kwargs,
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
        sys.exit(1)

    logger.info("=" * 60)
    logger.info("Entrenamiento completado exitosamente.")
    logger.info(f"Pesos optimos guardados en: {best_weights_path}")

    # Evaluacion final sobre el conjunto de test (datos nunca vistos)
    if has_test:
        logger.info("-" * 60)
        logger.info("Evaluando best.pt sobre el conjunto de TEST...")
        try:
            test_model = YOLO(str(best_weights_path))
            metrics = test_model.val(
                data=str(training_data_yaml),
                split="test",
                imgsz=args.imgsz,
                batch=args.batch,
                project=str(runs_dir / "detect"),
                name="test",
                exist_ok=True,
                **({"device": args.device} if args.device else {}),
            )
            logger.info(
                f"TEST -> mAP50: {metrics.box.map50:.4f} | mAP50-95: {metrics.box.map:.4f} | "
                f"Precision: {metrics.box.mp:.4f} | Recall: {metrics.box.mr:.4f}"
            )
        except Exception as exc:
            logger.warning(f"No se pudo evaluar sobre test: {exc}")

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
