# ToolLighthouse - Backend

API HTTP que recibe un fotograma JPEG, ejecuta YOLOv8 y devuelve la herramienta detectada. El frontend (Next.js) la consume a través de su proxy `/api/capture`.

- `POST /detect`: recibe la imagen (campo `image`) y devuelve `class`, `confidence`, `detections`, `boxes` y `model`.
- `GET /health`: devuelve `{"status": "ok", ...}` cuando el modelo ya cargó.

## Requisitos

- Python 3.10 o superior (comprueba con `python --version`).
- Los pesos del modelo, en el mismo lugar donde los busca `detect.py`, o indicados con la variable `WEIGHTS`.

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

## Conexión con el frontend

El frontend necesita saber dónde está este servidor: `BACKEND_URL=http://127.0.0.1:8000` en `frontend\.env.local`.
