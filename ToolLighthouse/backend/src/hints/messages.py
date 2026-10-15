"""Frases cerradas de los avisos de baja confianza (sin texto libre)."""

from __future__ import annotations

# clase -> (sustantivo con articulo, genero). El genero hace concordar "moverlo/moverla".
CLASS_INFO = {
    "cautin": ("un cautín", "m"),
    "multimetro_digital": ("un multímetro digital", "m"),
    "multimetro_analogico": ("un multímetro analógico", "m"),
    "estacion_soldadura": ("una estación de soldadura", "f"),
    "caja_sensores": ("una caja de sensores", "f"),
    "ventilador_pasco": ("un ventilador Pasco", "m"),
    "bascula": ("una báscula", "f"),
    "capacitor_variable": ("un capacitor variable", "m"),
    "fuente_voltaje": ("una fuente de voltaje", "f"),
}

CLASS_NAMES = tuple(CLASS_INFO)


def is_valid_class(class_name: object) -> bool:
    return isinstance(class_name, str) and class_name in CLASS_INFO


def build_hint_message(class_name: str) -> str:
    """Una unica frase a partir de una clase del inventario; lanza ValueError si no existe."""
    if not is_valid_class(class_name):
        raise ValueError("Clase no permitida para un aviso.")
    noun, gender = CLASS_INFO[class_name]
    pron = "lo" if gender == "m" else "la"
    return (
        f"Creo haber visto {noun}. "
        f"¿Podrías mover{pron} o colocar{pron} en otro ángulo para ver{pron} mejor?"
    )
