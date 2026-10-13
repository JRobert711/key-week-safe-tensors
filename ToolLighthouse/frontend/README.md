# ToolLighthouse — Frontend

Interfaz web (Next.js, App Router, TypeScript) que abre la cámara, captura un
fotograma y lo envía a un backend de detección a través de `/api/capture`.

El frontend es independiente de YOLO, del control UART y de la voz (Fish Audio).
La voz vive solo en el backend (`VoiceBridge`); `FISH_API_KEY` nunca llega al navegador.

## Requisitos

- Node.js 20 o superior
- Un backend con `POST /detect` (disponible a partir de la Fase 4)

## Puesta en marcha

```bash
npm install
cp .env.example .env.local
npm run dev
```

Abre http://localhost:3000. No se necesitan credenciales reales.

## Variables de entorno (solo servidor)

| Variable | Descripción | Por defecto |
| --- | --- | --- |
| `BACKEND_URL` | Dirección del backend de detección | `http://localhost:8000` |
| `MAX_UPLOAD_BYTES` | Tamaño máximo de la imagen | `1048576` |
| `BACKEND_TIMEOUT_MS` | Tiempo de espera al backend | `10000` |

Ninguna lleva el prefijo `NEXT_PUBLIC_`, por lo que no se incluyen en el código del cliente.

## Scripts

- `npm run dev`: desarrollo
- `npm run build`: compilación
- `npm run start`: ejecución de la compilación
- `npm run lint`: verificación de tipos

## Estructura

```
src/
  app/          páginas y Route Handlers (api/capture llega en la Fase 4)
  components/   CameraView, CaptureControls y CameraPanel (une hooks y vistas)
  hooks/        useCamera (cámara) y useCapture (captura, envío y estados)
  lib/          captureFrame.ts (canvas a JPEG), captureApi.ts (sendFrame, HTTP),
                server-config.ts (solo servidor)
  types/        contratos compartidos (DetectionResult)
```

## Estrategia de captura

Se envía un fotograma individual. El backend devuelve la mejor detección de
ese fotograma sin modificar el estado oficial de `detect.py`, sin enviar nada
por UART y sin activar anuncios de voz.
