"""
Configuracion de voz, leida SOLO desde el entorno (y opcionalmente de un .env).

Notas:
  - El .env se lee para la voz unicamente; NO se vuelca en os.environ, asi la
    configuracion del detector (CONF, WEIGHTS, ...) sigue comportandose igual.
  - Las variables reales del proceso tienen prioridad sobre el .env.
  - La API key no aparece en repr() ni en logs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping, Optional

DEFAULT_MODEL = "s2.1-pro-free"

_TRUE = {"1", "true", "yes", "on", "si", "sí"}
_FALSE = {"0", "false", "no", "off", ""}


class VoiceConfigError(ValueError):
    """Valor de configuracion invalido (el mensaje nunca incluye secretos)."""


def _get_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise VoiceConfigError(f"{key} debe ser true/false (recibido: {raw!r}).")


def _get_number(env: Mapping[str, str], key: str, default: float, lo: float, hi: float, *, integer: bool):
    raw = env.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw) if integer else float(raw)
    except ValueError:
        raise VoiceConfigError(f"{key} debe ser {'un entero' if integer else 'un numero'} (recibido: {raw!r}).") from None
    if not (lo <= value <= hi):
        raise VoiceConfigError(f"{key} debe estar entre {lo} y {hi} (recibido: {value}).")
    return value


def _merged_environ() -> dict:
    values: dict = {}
    try:
        from dotenv import dotenv_values, find_dotenv

        path = find_dotenv() or find_dotenv(usecwd=True)
        if path:
            values.update({k: v for k, v in dotenv_values(path).items() if v is not None})
    except ImportError:  # python-dotenv es opcional
        pass
    values.update(os.environ)
    return values


@dataclass(frozen=True)
class VoiceConfig:
    enabled: bool = True
    api_key: str = field(default="", repr=False)
    language: str = "es"
    model: str = DEFAULT_MODEL
    reference_id: Optional[str] = None
    request_timeout_seconds: float = 15.0
    max_queue_size: int = 20
    max_concurrent_synthesis: int = 1
    min_interval_seconds: float = 3.0
    max_retries: int = 1
    backend_key: str = field(default="", repr=False)
    cache_max_entries: int = 32
    dedupe_ttl_seconds: float = 120.0
    dedupe_max_entries: int = 64
    max_audio_bytes: int = 500_000

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None) -> "VoiceConfig":
        """Si se pasa `environ` se usa tal cual (pruebas); si no, entorno + .env."""
        env = environ if environ is not None else _merged_environ()
        return cls(
            enabled=_get_bool(env, "VOICE_ENABLED", True),
            api_key=env.get("FISH_API_KEY", "").strip(),
            language=(env.get("VOICE_LANGUAGE", "es").strip().lower() or "es"),
            model=env.get("VOICE_MODEL", "").strip() or DEFAULT_MODEL,
            reference_id=env.get("VOICE_REFERENCE_ID", "").strip() or None,
            request_timeout_seconds=_get_number(env, "VOICE_REQUEST_TIMEOUT_SECONDS", 15.0, 1, 120, integer=False),
            max_queue_size=_get_number(env, "VOICE_MAX_QUEUE_SIZE", 20, 1, 200, integer=True),
            max_concurrent_synthesis=_get_number(env, "VOICE_MAX_CONCURRENT_SYNTHESIS", 1, 1, 4, integer=True),
            min_interval_seconds=_get_number(env, "VOICE_MIN_INTERVAL_SECONDS", 3.0, 0, 60, integer=False),
            max_retries=_get_number(env, "VOICE_MAX_RETRIES", 1, 0, 3, integer=True),
            backend_key=env.get("VOICE_BACKEND_KEY", "").strip(),
            cache_max_entries=_get_number(env, "VOICE_CACHE_MAX_ENTRIES", 32, 1, 256, integer=True),
            dedupe_ttl_seconds=_get_number(env, "VOICE_DEDUPE_TTL_SECONDS", 120.0, 5, 3600, integer=False),
            dedupe_max_entries=_get_number(env, "VOICE_DEDUPE_MAX_ENTRIES", 64, 8, 1024, integer=True),
            max_audio_bytes=_get_number(env, "VOICE_MAX_AUDIO_BYTES", 500_000, 10_000, 5_000_000, integer=True),
        )
