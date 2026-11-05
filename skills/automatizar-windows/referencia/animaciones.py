"""Animaciones propias de Brumo para las luces (pedido del dueño: "animaciones chéveres, como las que tenía la RAM").

Los aparatos traen sus efectos de fábrica (arcoíris, lluvia, visor…), pero algunos no existen en todos (la cinta no
tiene fuego ni ola). Estas animaciones las calcula Brumo cuadro a cuadro y las manda por OpenRGB en modo "Direct",
LED por LED: la cinta de la placa tiene 60 LEDs direccionables y cada RAM 10. Corren en un hilo aparte (~20 cuadros por
segundo, gasto de procesador mínimo) y se detienen solas cuando se pide cualquier otra cosa a las luces.
"""
from __future__ import annotations

import colorsys
import logging
import math
import random
import threading
import time
from typing import Any, Callable

log = logging.getLogger(__name__)

Color = tuple[int, int, int]
CUADROS = 20  # por segundo


def _hsv(h: float, s: float = 1.0, v: float = 1.0) -> Color:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, s, max(0.0, min(1.0, v)))
    return round(r * 255), round(g * 255), round(b * 255)


def _mezclar(a: Color, b: Color, t: float) -> Color:
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))  # type: ignore[return-value]


class Fuego:
    """Llamas: cada LED titila entre rojo y amarillo; en una tira, el calor sube desde la base y se enfría arriba."""

    def __init__(self, n: int, color: Color | None = None):
        self.calor = [random.random() for _ in range(n)]
        self.n = n

    def cuadro(self, t: float) -> list[Color]:
        n = self.n
        for i in range(n):  # enfriar y avivar al azar
            self.calor[i] = max(0.0, self.calor[i] - random.uniform(0, 0.12))
            if random.random() < 0.35:
                self.calor[i] = min(1.0, self.calor[i] + random.uniform(0.2, 0.6))
        if n > 12:  # tira larga: el calor sube
            for i in range(n - 1, 1, -1):
                self.calor[i] = (self.calor[i] + self.calor[i - 1] * 1.6 + self.calor[i - 2]) / 3.7
        salida = []
        for c in self.calor:
            if c < 0.5:
                salida.append(_mezclar((40, 0, 0), (255, 30, 0), c * 2))
            else:
                salida.append(_mezclar((255, 30, 0), (255, 170, 20), (c - 0.5) * 2))
        return salida


class Latido:
    """Corazón: dos golpes de luz (pum-pum) y una pausa, unas 60 veces por minuto."""

    def __init__(self, n: int, color: Color | None = None):
        self.n, self.color = n, color or (255, 0, 30)

    def cuadro(self, t: float) -> list[Color]:
        f = t % 1.0
        golpe = math.exp(-((f - 0.08) / 0.05) ** 2) + 0.7 * math.exp(-((f - 0.3) / 0.06) ** 2)
        v = 0.06 + 0.94 * min(1.0, golpe)
        return [tuple(round(c * v) for c in self.color)] * self.n  # type: ignore[list-item]


class Ola:
    """Una ola que recorre las LEDs. Con color: TONOS de ese color (algo más oscuro, algo más cálido), sin apagarse
    nunca (pedido del dueño: "olas en tonos rojizos", nada de colores mezclados). Sin color: ola arcoíris suave."""

    def __init__(self, n: int, color: Color | None = None):
        self.n, self.color = n, color

    def cuadro(self, t: float) -> list[Color]:
        salida = []
        for i in range(self.n):
            fase = i / max(self.n, 1) - t * 0.35
            if self.color is None:
                salida.append(_hsv(fase))
            else:
                h, s, _ = colorsys.rgb_to_hsv(*(c / 255 for c in self.color))
                onda = math.sin(2 * math.pi * fase * 2)
                salida.append(_hsv(h + 0.04 * onda, s, 0.35 + 0.65 * (0.5 + 0.5 * onda)))
        return salida


class PulsoSuave:
    """Latido que NUNCA apaga la luz (pedido del dueño): está al 100 % y da dos bajones rápidos hasta el 50 %
    ("pum-pum"), luego una pausa a pleno brillo, y repite (un ciclo cada 1,6 s)."""

    MINIMO = 0.5
    CICLO = 1.6

    def __init__(self, n: int, color: Color | None = None):
        self.n, self.color = n, color or (255, 0, 0)

    def cuadro(self, t: float) -> list[Color]:
        f = (t % self.CICLO) / self.CICLO
        bajon = math.exp(-((f - 0.10) / 0.05) ** 2) + 0.8 * math.exp(-((f - 0.30) / 0.05) ** 2)
        v = 1 - (1 - self.MINIMO) * min(1.0, bajon)
        return [tuple(round(c * v) for c in self.color)] * self.n  # type: ignore[list-item]


class Fijo:
    def __init__(self, n: int, color: Color | None = None):
        self.n, self.color = n, color or (255, 255, 255)

    def cuadro(self, t: float) -> list[Color]:
        return [self.color] * self.n


class Aurora:
    """Aurora boreal: verdes, turquesas y violetas que se mezclan despacio."""

    def __init__(self, n: int, color: Color | None = None):
        self.n = n

    def cuadro(self, t: float) -> list[Color]:
        salida = []
        for i in range(self.n):
            x = i / max(self.n, 1)
            h = 0.38 + 0.22 * math.sin(2 * math.pi * (x * 0.8 + t * 0.07)) + 0.08 * math.sin(2 * math.pi * (x * 2.3 - t * 0.11))
            v = 0.45 + 0.55 * (0.5 + 0.5 * math.sin(2 * math.pi * (x * 1.7 + t * 0.13)))
            salida.append(_hsv(h, 0.85, v))
        return salida


class Estrellas:
    """Cielo con estrellas: fondo azul oscuro y LEDs que brillan y se apagan al azar."""

    def __init__(self, n: int, color: Color | None = None):
        self.n, self.brillo = n, [0.0] * n
        self.color = color or (255, 255, 230)

    def cuadro(self, t: float) -> list[Color]:
        for i in range(self.n):
            self.brillo[i] *= 0.88
            if random.random() < 0.02:
                self.brillo[i] = 1.0
        return [_mezclar((0, 0, 25), self.color, b) for b in self.brillo]


class Neon:
    """Paleta del fondo de pantalla (Talia, Cyberpunk): rojo profundo y rosa que se mezclan muy despacio, con un
    destello cian (el color de sus ojos) que cruza de vez en cuando. Nunca se apaga; un ciclo completo dura ~40 s."""

    ROJO, ROSA, CIAN = (200, 0, 18), (255, 40, 120), (0, 200, 220)

    def __init__(self, n: int, color: Color | None = None):
        self.n = n

    def cuadro(self, t: float) -> list[Color]:
        salida = []
        for i in range(self.n):
            x = i / max(self.n, 1)
            mezcla = 0.5 + 0.5 * math.sin(2 * math.pi * (x * 0.9 - t / 40))
            base = _mezclar(self.ROJO, self.ROSA, mezcla)
            destello = math.exp(-(((x - (t / 25) % 1.6 + 0.3) / 0.12) ** 2))  # pasa una vez cada 25 s
            salida.append(_mezclar(base, self.CIAN, 0.8 * destello))
        return salida


ANIMACIONES: dict[str, Callable[[int, Color | None], Any]] = {
    "fuego": Fuego, "latido": Latido, "ola": Ola, "aurora": Aurora, "estrellas": Estrellas, "pulso suave": PulsoSuave,
    "neon": Neon,
}

# "Ambiente" de un color (lo que hace "pon las luces en rojo"): todo del mismo color. La RAM con olas en tonos de ese
# color; la cinta y el mouse FIJOS (el dueño probó el latido en la cinta y no le gustó: "se ve raro el parpadeo";
# sigue disponible como efecto "pulso suave" si lo pide). Se elige por el nombre del aparato.
AMBIENTE = (("dram", Ola), ("vengeance", Ola), ("ram", Ola))
AMBIENTE_OTROS = Fijo


def ambiente(nombre_aparato: str):
    n = nombre_aparato.lower()
    return next((clase for clave, clase in AMBIENTE if clave in n), AMBIENTE_OTROS)


class Animador:
    """Un solo hilo para todas las animaciones. iniciar() reemplaza la que haya; detener() la apaga."""

    def __init__(self):
        self._hilo: threading.Thread | None = None
        self._parar = threading.Event()

    @property
    def activa(self) -> bool:
        return self._hilo is not None and self._hilo.is_alive()

    def detener(self) -> None:
        if self._hilo is not None:
            self._parar.set()
            self._hilo.join(timeout=2)
        self._hilo = None

    def iniciar(self, nombre: str, aparatos: Callable[[], list[Any]], color: Color | None = None) -> None:
        """nombre = una de ANIMACIONES, o "ambiente" (cada aparato con la suya, ver AMBIENTE)."""
        self.detener()
        self._parar = threading.Event()
        self._hilo = threading.Thread(target=self._correr, args=(nombre, aparatos, color, self._parar),
                                      name=f"luces-{nombre}", daemon=True)
        self._hilo.start()

    @staticmethod
    def _correr(nombre: str, aparatos: Callable[[], list[Any]], color: Color | None, parar: threading.Event) -> None:
        from openrgb.utils import RGBColor

        try:
            lista = aparatos()  # conexión propia a OpenRGB, para no pisarse con la del resto de Brumo
            animaciones = []
            for a in lista:
                directo = next((m for m in a.modes if m.name.lower() == "direct"), None)
                if directo is None:
                    continue
                a.set_mode(directo)
                clase = ambiente(a.name) if nombre == "ambiente" else ANIMACIONES[nombre]
                animaciones.append((a, clase(len(a.leds) or 1, color)))
            inicio = time.monotonic()
            while not parar.is_set():
                t = time.monotonic() - inicio
                for a, anim in animaciones:
                    a.set_colors([RGBColor(*c) for c in anim.cuadro(t)], fast=True)
                parar.wait(1 / CUADROS)
        except Exception:  # noqa: BLE001 - si OpenRGB se cae, la animación termina sin tumbar a Brumo
            log.exception("La animación de luces '%s' se detuvo", nombre)


ANIMADOR = Animador()
