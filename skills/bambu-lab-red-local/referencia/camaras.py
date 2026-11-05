"""Los ojos sobre las impresoras: fotos de la cámara de cada A1 y vigilancia de fallas con la IA de visión.

La A1 entrega fotos por la red local (puerto 6000, TLS, usuario "bblp" + código de acceso LAN). Requiere que en la
impresora esté activada la vista en vivo por LAN. Es SOLO lectura: Brumo mira y avisa; nunca pausa ni cancela.

Para no dar falsas alarmas (la IA de visión es pequeña), una falla se avisa solo si se ve en DOS fotos seguidas.
"""
from __future__ import annotations

import asyncio
import logging
import socket
import ssl
import struct
import time
from datetime import datetime
from pathlib import Path
from typing import Awaitable, Callable

from brumo.config import RAIZ
from brumo.impresoras import Impresora, Monitor, trabajo

log = logging.getLogger(__name__)

CARPETA = RAIZ / "data" / "camaras"
CADA = 10 * 60          # una foto por impresora cada 10 minutos mientras imprime
CONFIRMAR = 3 * 60      # si algo se ve raro, se vuelve a mirar a los 3 minutos
DESDE_CAPA = 3          # las primeras capas casi no se ven en la foto
PREGUNTA = (
    "Es la foto de la cámara de una impresora 3D mientras imprime. ¿Se ve una FALLA CLARA? Fallas: una maraña de hilos "
    "sueltos tipo espagueti, la pieza despegada, caída o corrida de la cama, o una bola de plástico pegada a la boquilla. "
    "Si la impresión se ve normal o no se distingue bien, es BIEN. Responde con una sola palabra al inicio, FALLA o BIEN, "
    "y después una frase corta de lo que ves."
)

AvisarFoto = Callable[[str, str | None, Path | None], Awaitable[None]]


def foto(imp: Impresora, espera: float = 10) -> bytes:
    """Una foto JPEG de la cámara de la impresora."""
    contexto = ssl.create_default_context()
    contexto.check_hostname = False
    contexto.verify_mode = ssl.CERT_NONE  # certificado propio de la impresora; tráfico dentro de la red local
    auth = struct.pack("<IIII", 0x40, 0x3000, 0, 0) + b"bblp".ljust(32, b"\0") + imp.codigo.encode().ljust(32, b"\0")
    with socket.create_connection((imp.ip, 6000), timeout=espera) as crudo, \
            contexto.wrap_socket(crudo, server_hostname=imp.ip) as s:
        s.sendall(auth)
        cabecera = _leer(s, 16)
        tamano = struct.unpack("<I", cabecera[:4])[0]
        if not 0 < tamano < 5_000_000:
            raise RuntimeError("la cámara respondió algo raro")
        imagen = _leer(s, tamano)
    if not imagen.startswith(b"\xff\xd8"):
        raise RuntimeError("la cámara no mandó una foto (¿está activada la vista en vivo por LAN?)")
    return imagen


def _leer(s, n: int) -> bytes:
    datos = b""
    while len(datos) < n:
        trozo = s.recv(n - len(datos))
        if not trozo:
            raise RuntimeError("la cámara cerró la conexión")
        datos += trozo
    return datos


def guardar(nombre: str, imagen: bytes) -> Path:
    """Se guarda solo la última foto de cada impresora (data/ no se sube)."""
    CARPETA.mkdir(parents=True, exist_ok=True)
    ruta = CARPETA / f"{''.join(c for c in nombre if c.isalnum()) or 'impresora'}.jpg"
    ruta.write_bytes(imagen)
    return ruta


def es_falla(respuesta: str) -> bool:
    return respuesta.strip().upper().lstrip("*¡! ").startswith("FALLA")


async def _foto_final(imp: Impresora, nombre_trabajo: str, avisar: AvisarFoto) -> None:
    """Al terminar, la foto de cómo quedó la pieza (sin voz: el aviso de que terminó ya se dijo)."""
    try:
        ruta = guardar(imp.nombre, await asyncio.to_thread(foto, imp))
    except Exception as e:  # noqa: BLE001
        log.info("Sin foto final de %s: %s", imp.nombre, e)
        return
    cual = f" «{nombre_trabajo}»" if nombre_trabajo else ""
    await avisar(f"📸 Así quedó{cual} en {imp.nombre}.", "", ruta)


async def vigilar(monitor: Monitor, ver: Callable[[bytes], Awaitable[str]], avisar: AvisarFoto, tz) -> None:
    """Mira cada impresora que esté imprimiendo y avisa (con la foto) si ve una falla dos veces seguidas.
    Cuando una termina, manda la foto de cómo quedó."""
    proxima: dict[str, float] = {}
    sospecha: dict[str, str] = {}
    avisado: set[str] = set()
    antes: dict[str, str] = {}
    while True:
        try:
            for imp in monitor.impresoras:
                estado = dict(monitor.estados.get(imp.nombre) or {})
                ahora_estado = str(estado.get("gcode_state") or "").upper()
                if ahora_estado == "FINISH" and antes.get(imp.nombre) in {"RUNNING", "PAUSE", "PREPARE"}:
                    await _foto_final(imp, trabajo(estado), avisar)
                antes[imp.nombre] = ahora_estado
                corriendo = ahora_estado == "RUNNING"
                clave = f"{imp.nombre}|{trabajo(estado)}"
                if not corriendo or int(estado.get("layer_num") or 0) < DESDE_CAPA:
                    sospecha.pop(imp.nombre, None)
                    continue
                if clave in avisado or time.monotonic() < proxima.get(imp.nombre, 0):
                    continue
                try:
                    imagen = await asyncio.to_thread(foto, imp)
                except Exception as e:  # noqa: BLE001
                    log.info("Sin foto de %s: %s", imp.nombre, e)
                    proxima[imp.nombre] = time.monotonic() + CADA
                    continue
                ruta = guardar(imp.nombre, imagen)
                respuesta = await ver(imagen)
                if not es_falla(respuesta):
                    sospecha.pop(imp.nombre, None)
                    proxima[imp.nombre] = time.monotonic() + CADA
                    continue
                if imp.nombre not in sospecha:
                    sospecha[imp.nombre] = respuesta
                    proxima[imp.nombre] = time.monotonic() + CONFIRMAR
                    continue
                avisado.add(clave)
                sospecha.pop(imp.nombre, None)
                que = respuesta.split(maxsplit=1)[1] if len(respuesta.split()) > 1 else ""
                cual = f" «{trabajo(estado)}»" if trabajo(estado) else ""
                hora = datetime.now(tz).strftime("%H:%M")
                await avisar(f"👀 Creo que {imp.nombre}{cual} está fallando ({hora}): {que} Mira la foto y revísala; "
                             "yo no la detengo.", f"Ojo, creo que {imp.nombre} está fallando. Te mandé la foto.", ruta)
        except Exception:  # noqa: BLE001 - la vigilancia nunca debe morir
            log.exception("Error vigilando las impresoras")
        await asyncio.sleep(30)
