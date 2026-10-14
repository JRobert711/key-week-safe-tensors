# ToolLighthouse - Backend

API HTTP que recibe un fotograma JPEG, ejecuta YOLOv8 y devuelve la herramienta detectada. El frontend (Next.js) la consume a través de su proxy `/api/capture`.

- `POST /detect`: recibe la imagen (campo `image`) y devuelve `class`, `confidence`, `detections`, `boxes` y `model`.
- `GET /health`: devuelve `{"status": "ok", ...}` cuando el modelo ya cargó.

## Requisitos

- Python 3.10 o superior (comprueba con `python --version`).
- Los pesos del modelo, en el mismo lugar donde los busca `detect.py`, o indicados con la variable `WEIGHTS`. Si todavía no tienes pesos, entrénalos con `train.py` (ver [Entrenamiento del modelo](#entrenamiento-del-modelo)).

## 1. Crear el entorno virtual

Desde la carpeta `backend`:

```bat
cd backend
python -m venv .venv
```

Actívalo:

```bat
:: CMD
.venv\Scripts\activate.bat

:: PowerShell
.venv\Scripts\Activate.ps1
```

Si PowerShell bloquea el script, usa CMD o ejecuta antes `Set-ExecutionPolicy -Scope Process RemoteSigned`.

Cuando está activo, el prompt empieza con `(.venv)`. Para salir: `deactivate`.

## 2. Instalar las librerías

Con el entorno activo:

```bat
python -m pip install --upgrade pip
pip install -r requirements.txt
```

`ultralytics` instala también PyTorch, así que la primera instalación es pesada y tarda unos minutos.

`server.py` importa `PROJECT_CLASSES` y `resolve_model_weights` desde `detect.py`. Si al arrancar aparece un `ModuleNotFoundError` de otra librería que `detect.py` usa en su cabecera (por ejemplo `pyserial`), instálala con `pip install <nombre>` y agrégala a `requirements.txt`.

## 3. Ejecutar

Con el entorno activo y desde la carpeta `backend`:

```bat
python src\server.py
```

Alternativa con uvicorn:

```bat
uvicorn src.server:app --host 127.0.0.1 --port 8000
```

El backend queda en `http://127.0.0.1:8000`. La primera vez tarda unos segundos en cargar y calentar el modelo; verifica que esté listo abriendo `http://127.0.0.1:8000/health`.

Desde la carpeta `ToolLighthouse` también puedes usar `iniciar-backend.bat`, que activa el entorno virtual y ejecuta el servidor.

## Variables de entorno (opcionales)

| Variable | Descripción | Por defecto |
|---|---|---|
| `WEIGHTS` | Ruta a un archivo `.pt` | Misma lógica que `detect.py` |
| `CONF` | Umbral de confianza | `0.70` |
| `MAX_UPLOAD_BYTES` | Tamaño máximo de la imagen | `1048576` (1 MiB) |
| `HOST` | Dirección de escucha | `127.0.0.1` |
| `PORT` | Puerto | `8000` |

Ejemplo en CMD:

```bat
set CONF=0.5
set WEIGHTS=C:\ruta\a\best.pt
python src\server.py
```

## Entrenamiento del modelo

`src\train.py` hace el fine-tuning de **YOLOv8 Large** (`yolov8l.pt`) con tu dataset de herramientas y equipos de laboratorio. Al terminar evalúa el modelo sobre un conjunto de test y exporta el mejor checkpoint a ONNX.

### Estructura esperada

`train.py` toma como raíz del proyecto la carpeta que contiene a `src` (es decir, `backend`), por lo que espera:

```
backend/
├── src/
│   └── train.py
├── dataset/                 ← exportado desde Label Studio en formato YOLO
│   ├── data.yaml
│   ├── images/{train,val,test}/
│   └── labels/{train,val,test}/
├── models/                  ← se crea solo (best.pt y best.onnx)
└── runs/                    ← lo crea Ultralytics
```

El archivo `data.yaml` debe incluir los nombres de las clases (`names`). Si ya tienes todas las imágenes repartidas solo en `train` y `val`, no hay problema: el script las reúne y las vuelve a dividir.

### Ejecutar

Con el entorno activo y desde la carpeta `backend`:

```bat
python src\train.py
```

Ejemplos:

```bat
:: GPU NVIDIA y más épocas
python src\train.py --device 0 --epochs 100

:: Otro reparto de datos (70% train, 15% val, 15% test)
python src\train.py --val-split 0.15 --test-split 0.15

:: Augmentation más agresivo
python src\train.py --degrees 20 --flipud 0.5 --mixup 0.2

:: Comportamiento clásico: sin re-split ni augmentation
python src\train.py --no-split --no-augment
```

### Split train / val / test

Por defecto el script reúne todas las imágenes de `images/train`, `images/val` e `images/test`, las mezcla con una semilla fija y las reparte en **70 % train / 20 % val / 10 % test**.

- No mueve ni copia imágenes: genera las listas `dataset\split\train.txt`, `val.txt` y `test.txt` y un `dataset\split\data_split.yaml` que usa el entrenamiento.
- Ultralytics busca las etiquetas reemplazando `/images/` por `/labels/` en cada ruta, así que se debe mantener la estructura estándar YOLO.
- El split es reproducible con la misma `--seed` (por defecto `42`). Si agregas imágenes nuevas, la distribución cambia y las métricas de test no son comparables con corridas anteriores.
- El reparto es aleatorio simple, no estratificado: con pocas imágenes por clase, alguna clase podría quedar sin ejemplos en val o test.
- Con `--no-split` se usa tu `data.yaml` tal cual y no se evalúa en test.

Tras entrenar, `best.pt` se evalúa sobre el conjunto de test y se muestran **mAP50, mAP50-95, precisión y recall**. Los resultados se guardan en `runs\detect\test`.

### Data augmentation

Se aplica durante el entrenamiento y cada parámetro se ajusta por CLI. `--no-augment` los pone todos en 0.

| Argumento | Descripción | Por defecto |
|---|---|---|
| `--hsv-h` | Variación de tono (0-1) | `0.015` |
| `--hsv-s` | Variación de saturación (0-1) | `0.7` |
| `--hsv-v` | Variación de brillo (0-1) | `0.4` |
| `--degrees` | Rotación aleatoria (grados) | `10` |
| `--translate` | Traslación (fracción de la imagen) | `0.1` |
| `--scale` | Escala aleatoria (± fracción) | `0.5` |
| `--shear` | Cizallamiento (grados) | `2` |
| `--perspective` | Perspectiva (0-0.001) | `0` |
| `--fliplr` | Probabilidad de flip horizontal | `0.5` |
| `--flipud` | Probabilidad de flip vertical | `0` |
| `--mosaic` | Probabilidad de mosaic | `1.0` |
| `--mixup` | Probabilidad de mixup | `0.1` |
| `--copy-paste` | Probabilidad de copy-paste (solo segmentación) | `0` |
| `--erasing` | Probabilidad de random erasing | `0.4` |
| `--close-mosaic` | Desactiva mosaic en las últimas N épocas | `10` |

Si tus fotos de herramientas son cenitales (tomadas desde arriba), subir `--flipud` a `0.5` suele ayudar.

### Otros parámetros

| Argumento | Descripción | Por defecto |
|---|---|---|
| `--weights` | Checkpoint base | `yolov8l.pt` |
| `--data` | Ruta a `data.yaml` | `dataset\data.yaml` |
| `--epochs` | Épocas máximas | `60` |
| `--batch` | Batch size (bajo para evitar falta de memoria con el modelo Large) | `4` |
| `--imgsz` | Resolución de entrada (px) | `640` |
| `--patience` | Épocas sin mejora antes de parar (early stopping) | `15` |
| `--optimizer` | Optimizador | `AdamW` |
| `--device` | `0` para GPU NVIDIA, `cpu` para CPU, vacío para automático | vacío |
| `--seed` | Semilla del split y del entrenamiento | `42` |
| `--val-split` | Fracción para validación | `0.2` |
| `--test-split` | Fracción para test | `0.1` |

### Resultados

- `runs\detect\train\weights\best.pt`: mejor checkpoint. Cada ejecución **sobrescribe** esta carpeta.
- `models\best.pt`: copia de conveniencia del mejor checkpoint.
- `models\best.onnx`: exportación a ONNX (la exportación puede instalar `onnx` automáticamente la primera vez).

Para que el servidor use el modelo recién entrenado, deja los pesos donde `detect.py` los busca o indícalos con la variable `WEIGHTS`:

```bat
set WEIGHTS=models\best.pt
python src\server.py
```

## Conexión con el frontend

El frontend necesita saber dónde está este servidor: `BACKEND_URL=http://127.0.0.1:8000` en `frontend\.env`.
