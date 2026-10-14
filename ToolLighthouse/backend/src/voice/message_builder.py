"""
Mapeo CERRADO de clase -> frase. No hay texto libre: solo las 9 clases del inventario.

Mantener sincronizado con PROJECT_CLASSES de detect.py (aqui no se importa a proposito,
para que la voz no arrastre cv2/serial). Las frases deterministas permiten cache por clase.
"""

from __future__ import annotations

from .provider import InvalidEventError

PHRASES = {
    "es": {
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
}

CLASS_NAMES = tuple(PHRASES["es"])
SUPPORTED_LANGUAGES = tuple(PHRASES)


def is_valid_class(class_name: object) -> bool:
    return isinstance(class_name, str) and class_name in PHRASES["es"]


def build_detection_message(class_name: str, language: str = "es") -> str:
    """Devuelve la frase de la clase o lanza InvalidEventError (-> HTTP 400)."""
    table = PHRASES.get(language) or PHRASES["es"]
    try:
        return table[class_name]
    except (KeyError, TypeError):
        raise InvalidEventError("class_name no es una clase permitida.") from None
