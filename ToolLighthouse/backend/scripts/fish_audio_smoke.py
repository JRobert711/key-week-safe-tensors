"""
ToolLighthouse - Fase 1: prueba AISLADA de Fish Audio.

No usa camara, YOLO, FastAPI ni UART. Solo valida el proveedor TTS:
  1. Sintesis de una frase fija en espanol (bytes, formato, tamano, latencia).
  2. Guarda el MP3 en ./audio_out/ para escucharlo manualmente.
  3. Errores: credenciales invalidas, timeout y desconexion.

Uso (desde la carpeta del backend, con FISH_API_KEY en el entorno o en .env):
    pip install "fish-audio-sdk>=1.3,<2" python-dotenv
    python scripts/fish_audio_smoke.py                 # prueba completa
    python scripts/fish_audio_smoke.py --all           # ademas, las 9 frases
    python scripts/fish_audio_smoke.py --only synth    # solo sintesis
    python scripts/fish_audio_smoke.py --only errors   # solo errores
    python scripts/fish_audio_smoke.py --open          # abre el MP3 al terminar

Variables (todas opcionales salvo FISH_API_KEY):
    FISH_API_KEY, VOICE_MODEL (por defecto s2-pro), VOICE_REFERENCE_ID,
    VOICE_REQUEST_TIMEOUT_SECONDS (por defecto 15)

La clave NUNCA se imprime ni se guarda.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:  # python-dotenv es opcional en esta prueba
    pass

from fishaudio import AsyncFishAudio
from fishaudio.core.request_options import RequestOptions
from fishaudio.exceptions import (
    APIError,
    AuthenticationError,
    FishAudioError,
    RateLimitError,
    ServerError,
)
from fishaudio.types import TTSConfig

PHRASES = {
    "cautin": "Cautín identificado.",
    "multimetro_digital": "Multímetro digital identificado.",
    "multimetro_analogico": "Multímetro analógico identificado.",
    "estacion_soldadura": "Estación de soldadura identificada.",
    "caja_sensores": "Caja de sensores identificada.",
    "ventilador_pasco": "Ventilador Pasco identificado.",
    "bascula": "Báscula identificada.",
    "capacitor_variable": "Capacitor variable identificado.",
    "fuente_voltaje": "Fuente de voltaje identificada.",
}
FIXED_CLASS = "cautin"
MP3_BITRATE = 128  # kbps (debe coincidir con TTSConfig.mp3_bitrate)
MAX_REASONABLE_BYTES = 200_000  # una frase corta no deberia acercarse a esto
OUT_DIR = Path("audio_out")  # agregar *.mp3 / audio_out/ al .gitignore

MODEL = os.getenv("VOICE_MODEL", "").strip() or "s2-pro"
REFERENCE_ID = os.getenv("VOICE_REFERENCE_ID", "").strip() or None
TIMEOUT_S = float(os.getenv("VOICE_REQUEST_TIMEOUT_SECONDS", "15"))

results = []  # (nombre, ok, detalle)


def report(name: str, ok: bool, detail: str) -> None:
    results.append((name, ok, detail))
    print(f"[{'OK ' if ok else 'FALLO'}] {name}: {detail}")


def looks_like_mp3(data: bytes) -> bool:
    """ID3v2 al inicio, o sincronizacion de frame MPEG (11 bits a 1)."""
    if data[:3] == b"ID3":
        return True
    return len(data) > 2 and data[0] == 0xFF and (data[1] & 0xE0) == 0xE0


def config() -> TTSConfig:
    return TTSConfig(format="mp3", mp3_bitrate=MP3_BITRATE, reference_id=REFERENCE_ID)


async def synth(client: AsyncFishAudio, text: str, timeout: float = TIMEOUT_S):
    """Devuelve (bytes, segundos). max_retries=0: se mide el comportamiento real."""
    t0 = time.perf_counter()
    audio = await client.tts.convert(
        text=text,
        config=config(),
        model=MODEL,
        request_options=RequestOptions(timeout=timeout, max_retries=0),
    )
    return audio, time.perf_counter() - t0


def validate_audio(name: str, audio, seconds: float, text: str) -> Path | None:
    if not isinstance(audio, (bytes, bytearray)) or len(audio) == 0:
        report(name, False, f"respuesta no es bytes o esta vacia ({type(audio).__name__})")
        return None
    problems = []
    if not looks_like_mp3(bytes(audio)):
        problems.append(f"cabecera no parece MP3 (primeros bytes: {bytes(audio[:4]).hex()})")
    if len(audio) > MAX_REASONABLE_BYTES:
        problems.append(f"tamano inusual ({len(audio)} bytes)")
    est = len(audio) * 8 / (MP3_BITRATE * 1000)
    if est < 0.3:
        problems.append(f"duracion estimada muy corta ({est:.2f}s)")
    OUT_DIR.mkdir(exist_ok=True)
    path = OUT_DIR / f"{name}.mp3"
    path.write_bytes(bytes(audio))
    detail = f'"{text}" -> {len(audio)} bytes, ~{est:.1f}s de audio, latencia {seconds:.2f}s -> {path}'
    if problems:
        detail += " | " + "; ".join(problems)
    report(name, not problems, detail)
    return path


async def test_synth(api_key: str, all_classes: bool) -> Path | None:
    client = AsyncFishAudio(api_key=api_key)
    first_path = None
    targets = PHRASES if all_classes else {FIXED_CLASS: PHRASES[FIXED_CLASS]}
    try:
        for cls, text in targets.items():
            try:
                audio, secs = await synth(client, text)
            except FishAudioError as exc:
                report(f"synth_{cls}", False, describe(exc))
                continue
            except Exception as exc:  # red, timeout, etc.
                report(f"synth_{cls}", False, f"{type(exc).__name__}")
                continue
            p = validate_audio(f"synth_{cls}", audio, secs, text)
            first_path = first_path or p
    finally:
        await client.close()
    return first_path


def describe(exc: Exception) -> str:
    """Descripcion segura: tipo + estado HTTP; nunca cabeceras ni la clave."""
    status = getattr(exc, "status", None)
    return f"{type(exc).__name__}" + (f" (HTTP {status})" if status else "")


async def test_errors() -> None:
    # 1) Credenciales invalidas: se espera 401 / AuthenticationError.
    bad = AsyncFishAudio(api_key="clave-invalida-de-prueba")
    try:
        await synth(bad, PHRASES[FIXED_CLASS])
        report("err_credenciales", False, "se esperaba error y la llamada tuvo exito")
    except AuthenticationError as exc:
        report("err_credenciales", True, f"detectado correctamente: {describe(exc)}")
    except APIError as exc:
        report("err_credenciales", getattr(exc, "status", 0) in (401, 403), f"APIError distinto: {describe(exc)}")
    except Exception as exc:
        report("err_credenciales", False, f"no se pudo llegar a la API: {type(exc).__name__}")

    # 2) Timeout: 1 ms es imposible de cumplir; se espera excepcion de timeout.
    real_key = os.getenv("FISH_API_KEY", "").strip() or "clave-invalida-de-prueba"
    slow = AsyncFishAudio(api_key=real_key)
    try:
        await synth(slow, PHRASES[FIXED_CLASS], timeout=0.001)
        report("err_timeout", False, "se esperaba timeout y la llamada tuvo exito")
    except Exception as exc:
        name = type(exc).__name__
        report("err_timeout", "Timeout" in name, f"excepcion: {name} (el backend debe mapearla a 504)")

    # 3) Desconexion: puerto local cerrado -> error de conexion inmediato.
    dead = AsyncFishAudio(api_key=real_key, base_url="http://127.0.0.1:9")
    try:
        await synth(dead, PHRASES[FIXED_CLASS], timeout=5)
        report("err_desconexion", False, "se esperaba error de conexion")
    except Exception as exc:
        name = type(exc).__name__
        report("err_desconexion", "Connect" in name or "Network" in name, f"excepcion: {name} (el backend debe mapearla a 502/503)")


def classify_for_backend() -> None:
    """Tabla informativa para la Fase 2 (no es una prueba)."""
    print("\nMapeo propuesto para Fase 2 (SDK -> HTTP del contrato):")
    for exc, http in [
        (AuthenticationError, "502 provider_error (credencial propia mala; no es culpa del cliente)"),
        (RateLimitError, "429 rate_limited"),
        (ServerError, "502 provider_error"),
        ("httpx.TimeoutException", "504 provider_timeout"),
        ("httpx.ConnectError", "502 provider_error"),
    ]:
        print(f"  {getattr(exc, '__name__', exc):28s} -> {http}")


async def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--all", action="store_true", help="sintetiza las 9 frases")
    ap.add_argument("--only", choices=["synth", "errors"], help="ejecuta solo un bloque")
    ap.add_argument("--open", action="store_true", help="abre el primer MP3 generado")
    args = ap.parse_args()

    api_key = os.getenv("FISH_API_KEY", "").strip()
    print(f"Modelo: {MODEL} | voz (reference_id): {'definida' if REFERENCE_ID else 'por defecto'} | timeout: {TIMEOUT_S}s\n")

    path = None
    if args.only in (None, "synth"):
        if not api_key:
            report("synth", False, "FISH_API_KEY no esta definida (entorno o .env)")
        else:
            path = await test_synth(api_key, args.all)
    if args.only in (None, "errors"):
        await test_errors()
    classify_for_backend()

    failed = [n for n, ok, _ in results if not ok]
    print(f"\nResumen: {len(results) - len(failed)}/{len(results)} OK" + (f" | fallos: {', '.join(failed)}" if failed else ""))
    if path:
        print(f"Escucha manual: {path.resolve()}")
        if args.open and hasattr(os, "startfile"):
            os.startfile(path)  # solo Windows
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
