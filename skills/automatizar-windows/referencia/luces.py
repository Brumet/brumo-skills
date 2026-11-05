"""Luces RGB del PC (cinta LED de la placa, memorias RAM...) a través de OpenRGB.

OpenRGB corre aparte, como administrador, con su servidor encendido (puerto 6742, solo en este equipo);
lo deja así setup/windows/instalar-luces.ps1. Brumo solo le habla: encender, apagar, color, brillo y efectos.
"""
from __future__ import annotations

from typing import Any

from brumo.negocio.pedidos import normalizar

PUERTO = 6742

COLORES = {
    "rojo": (255, 0, 0), "verde": (0, 255, 0), "azul": (0, 0, 255), "blanco": (255, 255, 255),
    "amarillo": (255, 200, 0), "naranja": (255, 100, 0), "morado": (140, 0, 255), "violeta": (140, 0, 255),
    "rosado": (255, 60, 150), "rosa": (255, 60, 150), "cian": (0, 255, 255), "turquesa": (0, 200, 170),
    "celeste": (80, 170, 255), "dorado": (255, 170, 30), "calido": (255, 140, 60), "frio": (150, 200, 255),
}
# Efectos de fábrica que entiende la gente → nombres que usan los aparatos en OpenRGB (el primero que exista). Los
# últimos de cada lista son el parecido para el aparato que no lo trae (la cinta no tiene lluvia ni visor: cambia de
# color). Las animaciones que calcula Brumo (fuego, latido, ola, aurora, estrellas) están en animaciones.py.
EFECTOS = {
    "fijo": ["Static", "Direct"], "respirar": ["Breathing", "Color Pulse"],
    "arcoiris": ["Rainbow Wave", "Spectrum Cycle", "Rainbow", "Color Cycle"],
    "ciclo": ["Spectrum Cycle", "Color Cycle", "Color Shift"], "parpadeo": ["Flashing", "Flash", "Strobe"],
    "lluvia": ["Rain", "Random", "Color Cycle", "Spectrum Cycle"], "visor": ["Visor", "Color Cycle", "Spectrum Cycle"],
    "marquesina": ["Marquee", "Double Flash", "Color Cycle"], "secuencial": ["Sequential", "Color Cycle"],
    "pulso": ["Color Pulse", "Breathing"], "aleatorio": ["Random", "Color Shift", "Spectrum Cycle"],
    "doble parpadeo": ["Double Flash", "Flashing"], "cambio de color": ["Color Shift", "Color Cycle", "Spectrum Cycle"],
    "ola de color": ["Color Wave", "Rainbow Wave", "Color Cycle"],
}
# Efectos que usan el color elegido (si no dicen color, blanco); los demás traen sus propios colores.
CON_COLOR = {"fijo", "respirar", "parpadeo", "pulso", "doble parpadeo", "ola de color"}


def color(nombre: str) -> tuple[int, int, int] | None:
    n = normalizar(nombre).replace(" ", "")
    if n.startswith("#") and len(n) == 7:
        return tuple(int(n[i:i + 2], 16) for i in (1, 3, 5))  # type: ignore[return-value]
    return COLORES.get(n)


def escalar(rgb: tuple[int, int, int], brillo: int) -> tuple[int, int, int]:
    f = max(0, min(100, brillo)) / 100
    return tuple(round(c * f) for c in rgb)  # type: ignore[return-value]


def modo(aparato: Any, efecto: str) -> Any | None:
    nombres = {m.name.lower(): m for m in aparato.modes}
    for candidato in EFECTOS.get(efecto, [efecto]):
        if candidato.lower() in nombres:
            return nombres[candidato.lower()]
    return None


class Luces:
    def __init__(self, cliente: Any = None):
        self._cliente = cliente

    @property
    def cliente(self):
        if self._cliente is None:
            from openrgb import OpenRGBClient

            self._cliente = OpenRGBClient("127.0.0.1", PUERTO, "Brumo")
        return self._cliente

    def aparatos(self, cual: str = "") -> list[Any]:
        todos = list(self.cliente.devices)
        if not cual:
            return todos
        palabras = {"ram": ("dram", "memory", "vengeance"), "memoria": ("dram", "memory", "vengeance"),
                    "placa": ("motherboard", "fusion", "gigabyte"), "cinta": ("motherboard", "fusion", "gigabyte", "led strip")}
        claves = palabras.get(normalizar(cual), (normalizar(cual),))
        return [a for a in todos if any(k in f"{a.name} {a.type.name}".lower() for k in claves)]

    def pintar(self, rgb: tuple[int, int, int] | None, cual: str = "", efecto: str = "fijo") -> list[str]:
        """rgb=None: el efecto con sus propios colores (arcoíris, lluvia…), sin pisarlos con un color."""
        from openrgb.utils import RGBColor

        hechos = []
        for a in self.aparatos(cual):
            m = modo(a, efecto) or modo(a, "fijo")
            if m is not None:
                a.set_mode(m)
            if rgb is not None:
                a.set_color(RGBColor(*rgb))
            hechos.append(a.name)
        return hechos
