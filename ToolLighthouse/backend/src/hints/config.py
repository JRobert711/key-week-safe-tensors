"""
Configuracion de los avisos de baja confianza, leida SOLO desde el entorno (y opcionalmente de un .env).

Notas:
  - El .env se lee para los avisos unicamente; NO se vuelca en os.environ (misma regla que la voz).
  - Las variables reales del proceso tienen prioridad sobre el .env.
  - Un valor invalido nunca tumba el servidor: HintConfigError -> server.py desactiva los avisos y
    la deteccion sigue igual.
  - CONF lo sigue leyendo server.py solo desde el entorno (sin cambios); aqui solo se recibe para validar.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping, Optional, Tuple
from urllib.parse import urlsplit

_TRUE = {"1", "true", "yes", "on", "si", "sí"}
_FALSE = {"0", "false", "no", "off", ""}


class HintConfigError(ValueError):
    """Valor de configuracion invalido."""


def _get_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    raw = env.get(key)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in _TRUE:
        return True
    if value in _FALSE:
        return False
    raise HintConfigError(f"{key} debe ser true/false (recibido: {raw!r}).")


def _get_number(env: Mapping[str, str], key: str, default: float, lo: float, hi: float, *, integer: bool):
    raw = env.get(key)
    if raw is None or not raw.strip():
        return default
    try:
        value = int(raw) if integer else float(raw)
    except ValueError:
        raise HintConfigError(
            f"{key} debe ser {'un entero' if integer else 'un numero'} (recibido: {raw!r})."
        ) from None
    if not (lo <= value <= hi):
        raise HintConfigError(f"{key} debe estar entre {lo} y {hi} (recibido: {value}).")
    return value


def _get_origins(env: Mapping[str, str], key: str) -> Tuple[str, ...]:
    """Lista separada por comas de origenes http(s) exactos (sin comodines)."""
    raw = env.get(key, "")
    origins = []
    for part in raw.split(","):
        value = part.strip().rstrip("/").lower()
        if not value:
            continue
        parsed = urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path or parsed.query:
            raise HintConfigError(f"{key} contiene un origen invalido: {part.strip()!r} (use http(s)://host[:puerto]).")
        origins.append(f"{parsed.scheme}://{parsed.netloc}")
    return tuple(dict.fromkeys(origins))


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
class HintConfig:
    enabled: bool = True
    low_conf: float = 0.30
    window: int = 10
    min_frames: int = 5
    cooldown_seconds: float = 15.0
    global_cooldown_seconds: float = 10.0
    backoff_factor: float = 2.0
    cooldown_max_seconds: float = 120.0
    registry_ttl_seconds: float = 600.0
    # Feedback de falsos positivos (Fase 5)
    feedback_enabled: bool = True
    fp_mute_seconds: float = 600.0
    feedback_log: str = "data/hint_feedback.jsonl"
    save_frames: bool = False
    # WebSocket (Fase 2)
    backend_key: str = field(default="", repr=False)
    ws_allowed_origins: Tuple[str, ...] = ()
    ws_ticket_ttl_seconds: float = 30.0
    ws_max_clients: int = 8

    @classmethod
    def from_env(cls, environ: Optional[Mapping[str, str]] = None, *, conf: float = 0.60) -> "HintConfig":
        """
        `conf` es el umbral de deteccion confirmada (CONF de server.py); LOW_CONF debe ser menor.
        Si se pasa `environ` se usa tal cual (pruebas); si no, entorno + .env.
        Con HINTS_ENABLED=false no se valida nada mas.
        """
        env = environ if environ is not None else _merged_environ()
        enabled = _get_bool(env, "HINTS_ENABLED", True)
        if not enabled:
            return cls(enabled=False)

        low_conf = _get_number(env, "LOW_CONF", 0.30, 0.05, 1.0, integer=False)
        if not low_conf < conf:
            raise HintConfigError(f"LOW_CONF ({low_conf}) debe ser menor que CONF ({conf}).")

        window = _get_number(env, "HINT_WINDOW", 10, 3, 50, integer=True)
        min_frames = _get_number(env, "HINT_MIN_FRAMES", min(5, window), 1, 50, integer=True)
        if min_frames > window:
            raise HintConfigError(f"HINT_MIN_FRAMES ({min_frames}) no puede superar HINT_WINDOW ({window}).")

        return cls(
            enabled=True,
            low_conf=low_conf,
            window=window,
            min_frames=min_frames,
            cooldown_seconds=_get_number(env, "HINT_COOLDOWN_SECONDS", 15.0, 0, 600, integer=False),
            global_cooldown_seconds=_get_number(env, "HINT_GLOBAL_COOLDOWN_SECONDS", 10.0, 0, 600, integer=False),
            backoff_factor=_get_number(env, "HINT_BACKOFF_FACTOR", 2.0, 1, 5, integer=False),
            cooldown_max_seconds=_get_number(env, "HINT_COOLDOWN_MAX_SECONDS", 120.0, 0, 3600, integer=False),
            registry_ttl_seconds=_get_number(env, "HINT_REGISTRY_TTL_SECONDS", 600.0, 30, 86400, integer=False),
            feedback_enabled=_get_bool(env, "HINT_FEEDBACK_ENABLED", True),
            fp_mute_seconds=_get_number(env, "HINT_FP_MUTE_SECONDS", 600.0, 0, 86400, integer=False),
            feedback_log=(env.get("HINT_FEEDBACK_LOG", "").strip() or "data/hint_feedback.jsonl"),
            save_frames=_get_bool(env, "HINT_SAVE_FRAMES", False),
            # Misma credencial X-Backend-Key que la voz, salvo que se defina una propia para los avisos.
            backend_key=(env.get("HINT_BACKEND_KEY", "").strip() or env.get("VOICE_BACKEND_KEY", "").strip()),
            ws_allowed_origins=_get_origins(env, "WS_ALLOWED_ORIGINS"),
            ws_ticket_ttl_seconds=_get_number(env, "WS_TICKET_TTL_SECONDS", 30.0, 5, 300, integer=False),
            ws_max_clients=_get_number(env, "WS_MAX_CLIENTS", 8, 1, 100, integer=True),
        )

    def summary(self) -> str:
        return (
            f"low_conf={self.low_conf:.2f} ventana={self.min_frames}/{self.window} "
            f"espera_clase={self.cooldown_seconds:g}s espera_global={self.global_cooldown_seconds:g}s "
            f"backoff=x{self.backoff_factor:g} tope={self.cooldown_max_seconds:g}s "
            f"feedback={'si' if self.feedback_enabled else 'no'} silencio_fp={self.fp_mute_seconds:g}s "
            f"guardar_fotogramas={'si' if self.save_frames else 'no'} "
            f"ws_clientes<={self.ws_max_clients} ticket={self.ws_ticket_ttl_seconds:g}s "
            f"auth={'X-Backend-Key' if self.backend_key else 'ninguna'} "
            f"origenes={','.join(self.ws_allowed_origins) or 'cualquiera'}"
        )
