# ToolLighthouse — integración de voz, Fase 3

## Variables de entorno

Copia `.env.example` a `.env.local` en la raíz del frontend y ajusta `BACKEND_URL`. Si el backend usa autenticación para las rutas de voz, configura `VOICE_BACKEND_KEY` en Next.js con el mismo valor que `VOICE_BACKEND_KEY` del backend. La clave se usa únicamente en el servidor y no se entrega al navegador.

- `VOICE_AUDIO_PATH`: ruta interna del backend, por defecto `/voice/audio`.
- `VOICE_BACKEND_TIMEOUT_MS`: timeout del proxy de voz, por defecto 35 segundos.
- `VOICE_MAX_AUDIO_BYTES`: límite del audio recibido, por defecto 500 000 bytes.

El botón **Probar voz** solicita la frase fija de `cautin`. La reproducción automática de detecciones estables queda fuera de esta fase; el modo de detección y el contrato `/detect` no se modifican.

## Prueba manual

Con FastAPI y Next.js en ejecución, pulsa **Probar voz** en la interfaz. Si el navegador bloquea la reproducción tras la espera de síntesis, pulsa **Reproducir audio** para autorizarla mediante un gesto explícito.

También se puede probar el proxy desde la terminal:

```sh
curl -i http://localhost:3000/api/voice/audio \
  -H 'Content-Type: application/json' \
  -d '{"event_id":"event_test_001","session_id":"session_test_001","class_name":"cautin","transition_id":0}' \
  --output voz-prueba.mp3
```

La primera petición puede tardar por la síntesis; las peticiones demasiado seguidas pueden responder `429` debido al límite de frecuencia del backend.

## Alcance de los cambios

- Proxy same-origin `POST /api/voice/audio` con validación estricta, límite de cuerpo/audio, timeout, errores normalizados y cabeceras de seguridad.
- Contrato y cliente HTTP de voz, con cancelación y timeout.
- Hook de reproducción con limpieza de URL temporal, cancelación al desmontar y reintento manual si el navegador bloquea autoplay.
- Botón de prueba aislado de los estados de captura y detección.
