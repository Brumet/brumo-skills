"""Detector de fallas de impresión 3D de Obico (antes "The Spaghetti Detective").

Es una red entrenada SOLO para encontrar fallas en fotos de impresoras (espagueti, pieza despegada, bolas de
plástico), mucho más confiable que preguntarle a un modelo general de visión. Corre en el procesador en menos de
un segundo, así que no le quita la tarjeta de video al resto de Brumo.

El modelo (~200 MB) no viene con Brumo: se baja una vez con `python setup/obico_descargar.py` desde la página
oficial de Obico (github.com/TheSpaghettiDetective/obico-server). Sin él, la vigilancia usa la IA de visión.
"""
from __future__ import annotations

import io
import logging
import threading

from brumo.config import RAIZ

log = logging.getLogger(__name__)

MODELO = RAIZ / "data" / "modelos_ia" / "obico.onnx"
UMBRAL = 0.30        # confianza mínima para decir "falla" (el mismo valor por defecto de obico-bambu)
MINIMO = 0.20        # por debajo de esto ni se cuenta
_sesion = None
_candado = threading.Lock()


def disponible() -> bool:
    if not MODELO.exists():
        return False
    try:
        import onnxruntime  # noqa: F401
    except ImportError:
        return False
    return True


def _cargar():
    global _sesion
    with _candado:
        if _sesion is None:
            import onnxruntime as ort

            _sesion = ort.InferenceSession(str(MODELO), providers=["CPUExecutionProvider"])
            log.info("Detector de fallas de Obico listo")
    return _sesion


def confianza(imagen: bytes) -> float:
    """De 0 a 1: qué tan seguro está de que hay una falla en la foto (la detección más fuerte)."""
    import numpy as np
    from PIL import Image

    sesion = _cargar()
    entrada = sesion.get_inputs()[0]
    _, _, alto, ancho = entrada.shape
    foto = Image.open(io.BytesIO(imagen)).convert("RGB").resize((int(ancho), int(alto)))
    blob = (np.asarray(foto, dtype=np.float32) / 255.0).transpose(2, 0, 1)[np.newaxis]
    salidas = sesion.run(None, {entrada.name: blob})
    confs = np.asarray(salidas[1]).squeeze(0)
    mayor = float(confs.max()) if confs.size else 0.0
    return mayor if mayor >= MINIMO else 0.0


def respuesta(imagen: bytes) -> str:
    """En el mismo formato que la IA de visión ("FALLA …" / "BIEN …"), para la vigilancia de cámaras."""
    c = confianza(imagen)
    if c >= UMBRAL:
        return f"FALLA el detector de Obico ve espagueti o una pieza dañada (seguro al {c * 100:.0f} %)."
    return f"BIEN sin señales de falla ({c * 100:.0f} %)."
