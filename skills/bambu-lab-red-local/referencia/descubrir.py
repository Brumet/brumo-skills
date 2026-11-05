"""Encontrar las impresoras sin que el dueño tenga que copiar nada.

- Número de serie y código de acceso LAN: se leen de Bambu Studio en este PC (BambuStudio.conf, la sección
  "access_code" que Bambu Studio guarda al conectarse a cada impresora). No se copian a .env ni a ningún lado.
- IP: se busca en la red local quién tiene abierto el puerto de las Bambu (8883) y se prueba con qué código entra;
  el código que funciona dice cuál impresora es. Si la IP cambia (el router la reasigna), se vuelve a buscar.
Lo encontrado (IP y nombre, sin códigos) se recuerda en data/impresoras.json para conectar rápido al arrancar.
"""
from __future__ import annotations

import asyncio
import ipaddress
import json
import logging
import os
import socket
import ssl
import threading
from pathlib import Path

from brumo.config import RAIZ

log = logging.getLogger(__name__)

CONF = Path(os.environ.get("APPDATA", "")) / "BambuStudio" / "BambuStudio.conf"
RECUERDO = RAIZ / "data" / "impresoras.json"
PUERTO = 8883


def desde_bambu_studio(conf: Path | None = None) -> dict[str, str]:
    """{serie: código} de las impresoras que Bambu Studio conoce. Vacío si no hay Bambu Studio."""
    try:
        texto = (conf or CONF).read_text(encoding="utf-8")
        datos = json.loads(texto[: texto.rfind("}") + 1])  # al final trae una línea de control (MD5)
    except (OSError, ValueError):
        return {}
    codigos = datos.get("access_code") or {}
    return {str(s): str(c) for s, c in codigos.items() if s and c}


def recordar(conf: Path | None = None) -> dict[str, dict]:
    try:
        return json.loads((conf or RECUERDO).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def guardar(serie: str, ip: str, nombre: str, archivo: Path | None = None) -> None:
    archivo = archivo or RECUERDO
    datos = recordar(archivo)
    datos[serie] = {"ip": ip, "nombre": nombre}
    archivo.parent.mkdir(parents=True, exist_ok=True)
    archivo.write_text(json.dumps(datos, ensure_ascii=False, indent=1), encoding="utf-8")


def redes_locales() -> list[ipaddress.IPv4Network]:
    """La red /24 de cada IP local del PC (la de la casa / el taller)."""
    redes = set()
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if not ip.startswith(("127.", "169.254.")):
                redes.add(ipaddress.ip_network(f"{ip}/24", strict=False))
    except OSError:
        pass
    return sorted(redes)


async def _abierto(ip: str, puerto: int, espera: float) -> str | None:
    try:
        _, w = await asyncio.wait_for(asyncio.open_connection(ip, puerto), espera)
        w.close()
        return ip
    except (OSError, asyncio.TimeoutError):
        return None


def escanear(redes=None, puerto: int = PUERTO, espera: float = 1.5) -> list[str]:
    """IPs de la red local que tienen abierto el puerto de las Bambu."""
    ips = [str(h) for red in (redes or redes_locales()) for h in red.hosts()]

    async def todo():
        return await asyncio.gather(*(_abierto(ip, puerto, espera) for ip in ips))

    return [ip for ip in asyncio.run(todo()) if ip]


def entra(ip: str, codigo: str, espera: float = 6) -> bool:
    """¿La impresora de esa IP acepta este código? (solo se conecta y se desconecta; no manda nada)."""
    import paho.mqtt.client as mqtt

    listo, ok = threading.Event(), []
    cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"brumo-busca-{os.getpid()}")
    cliente.username_pw_set("bblp", codigo)
    cliente.tls_set(cert_reqs=ssl.CERT_NONE)
    cliente.tls_insecure_set(True)

    def al_conectar(_c, _d, _f, rc, _p):
        ok.append(not rc.is_failure)
        listo.set()

    cliente.on_connect = al_conectar
    try:
        cliente.connect(ip, PUERTO, keepalive=10)
    except OSError:
        return False
    cliente.loop_start()
    listo.wait(espera)
    cliente.disconnect()
    cliente.loop_stop()
    return bool(ok and ok[0])


def identificar(ips: list[str], codigos: dict[str, str], prueba=entra) -> dict[str, str]:
    """{serie: ip}: para cada IP, el código que entra dice qué impresora es."""
    hallado: dict[str, str] = {}
    for ip in ips:
        for serie, codigo in codigos.items():
            if serie not in hallado and prueba(ip, codigo):
                hallado[serie] = ip
                break
    return hallado
