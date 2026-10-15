"""Construcción cerrada de frases para conjuntos de objetos detectados."""

from __future__ import annotations

from .provider import InvalidEventError

# Orden estable: el mismo conjunto produce la misma frase y clave de caché.
PHRASES = {
    "es": {
        "cautin": "cautín",
        "multimetro_digital": "multímetro digital",
        "multimetro_analogico": "multímetro analógico",
        "estacion_soldadura": "estación de soldadura",
        "caja_sensores": "caja de sensores",
        "ventilador_pasco": "ventilador Pasco",
        "bascula": "báscula",
        "capacitor_variable": "capacitor variable",
        "fuente_voltaje": "fuente de voltaje",
    }
}

CLASS_NAMES = tuple(PHRASES["es"])
SUPPORTED_LANGUAGES = tuple(PHRASES)


def is_valid_class(class_name: object) -> bool:
    return isinstance(class_name, str) and class_name in CLASS_NAMES


def normalize_class_names(class_names: object) -> tuple[str, ...]:
    """Valida un conjunto y devuelve clases únicas en orden estable."""
    if not isinstance(class_names, (list, tuple)) or not class_names:
        raise InvalidEventError("class_names debe contener al menos una clase.")
    if any(not is_valid_class(name) for name in class_names):
        raise InvalidEventError("class_names contiene una clase no permitida.")
    selected = set(class_names)
    return tuple(name for name in CLASS_NAMES if name in selected)


def build_detection_message(class_names: object, language: str = "es") -> str:
    """Construye una única frase a partir de un conjunto cerrado de clases."""
    normalized = normalize_class_names(class_names)
    table = PHRASES.get(language) or PHRASES["es"]
    names = [table[name] for name in normalized]
    if len(names) == 1:
        return f"Detectado: {names[0]}."
    if len(names) == 2:
        joined = f"{names[0]} y {names[1]}"
    else:
        joined = f"{', '.join(names[:-1])} y {names[-1]}"
    return f"Detectados: {joined}."
