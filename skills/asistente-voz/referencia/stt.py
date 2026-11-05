"""Voz a texto local con faster-whisper (se carga la primera vez que se usa).

Si hay una tarjeta gráfica NVIDIA la usa (unas 10 veces más rápido: ~0,2 s por frase) y con ella
un modelo más preciso; si no (como en el mini PC), usa el procesador.
"""
from __future__ import annotations

import asyncio
import glob
import logging
import os
import sys
import threading
from pathlib import Path

log = logging.getLogger(__name__)

# Discord y el micrófono comparten el mismo modelo: cargarlo dos veces gasta memoria de más.
_MODELOS: dict[str, object] = {}
_LOCK = threading.Lock()


def _librerias_cuda() -> None:
    """En Windows, las librerías de CUDA vienen en paquetes pip (nvidia-cublas-cu12, nvidia-cudnn-cu12)."""
    if sys.platform != "win32":
        return
    for carpeta in glob.glob(os.path.join(sys.prefix, "Lib", "site-packages", "nvidia", "*", "bin")):
        if carpeta not in os.environ["PATH"]:
            os.environ["PATH"] = carpeta + os.pathsep + os.environ["PATH"]


def _cargar_modelo(modelo_cpu: str, modelo_gpu: str):
    from faster_whisper import WhisperModel

    try:
        import ctranslate2

        if ctranslate2.get_cuda_device_count() > 0:
            _librerias_cuda()
            m = WhisperModel(modelo_gpu, device="cuda", compute_type="float16")
            import numpy as np

            list(m.transcribe(np.zeros(16000, dtype=np.float32), language="es")[0])  # falla aquí si falta CUDA
            log.info("Whisper '%s' en la tarjeta gráfica", modelo_gpu)
            return m
    except Exception as e:  # noqa: BLE001 - sin GPU o sin librerías: se usa el procesador
        log.info("Whisper sin tarjeta gráfica (%s); uso el procesador", str(e)[:80])
    log.info("Cargando Whisper '%s' en el procesador (la primera vez lo descarga)...", modelo_cpu)
    return WhisperModel(modelo_cpu, device="cpu", compute_type="int8")


class Transcriptor:
    def __init__(self, modelo: str = "small", idioma: str = "es", modelo_gpu: str = "medium"):
        self.nombre_modelo = modelo
        self.modelo_gpu = modelo_gpu
        self.idioma = idioma

    def _cargar(self):
        clave = f"{self.nombre_modelo}|{self.modelo_gpu}"
        with _LOCK:
            if clave not in _MODELOS:
                _MODELOS[clave] = _cargar_modelo(self.nombre_modelo, self.modelo_gpu)
            return _MODELOS[clave]

    def _transcribir(self, audio) -> str:
        # `audio` puede ser una ruta o un arreglo de muestras a 16 kHz.
        # Ojo: no se le da una pista con el nombre ("Brumo."): Whisper la toma como ya dicha y se salta el nombre.
        segmentos, _ = self._cargar().transcribe(audio if not isinstance(audio, Path) else str(audio),
                                                 language=self.idioma, vad_filter=True, beam_size=1)
        return " ".join(s.text.strip() for s in segmentos).strip()

    async def transcribir(self, ruta: Path) -> str:
        return await asyncio.to_thread(self._transcribir, ruta)

    def transcribir_audio(self, audio) -> str:
        return self._transcribir(audio)

    def precargar(self) -> None:
        """Carga el modelo de una vez, para que la primera frase no tarde."""
        self._cargar()
