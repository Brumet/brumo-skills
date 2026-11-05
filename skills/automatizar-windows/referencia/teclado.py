"""Luces del teclado Arzopa K81 (chip SinoWealth, USB 258A:002A).

No lo soporta OpenRGB, así que se descubrió a mano (oct-2026), solo LEYENDO el teclado mientras se cambiaban
las luces con Fn: el buzón de configuración 0x0A (41 bytes) guarda el efecto en el byte 1 y el brillo (0-5)
en el byte 2. Se escriben SOLO efectos que el teclado mismo usó; el color no está en ese buzón (cada efecto
trae sus colores) y no se intenta adivinar dónde está.
"""
from __future__ import annotations

VENDOR, PRODUCTO, BUZON, LARGO = 0x258A, 0x002A, 0x0A, 42
# Efectos vistos en el teclado, en el orden en que los recorre Fn (números para hablar de ellos).
EFECTOS = [0x03, 0x05, 0x06, 0x07, 0x09, 0x0A, 0x0E, 0x10, 0x11, 0x13, 0x15, 0x16, 0x17, 0x1E]


def _abrir():
    import hid

    for d in hid.enumerate(VENDOR, PRODUCTO):
        if d["usage_page"] != 0xFF00:
            continue
        h = hid.device()
        h.open_path(d["path"])
        if h.get_report_descriptor()[8] == BUZON:
            return h
        h.close()
    raise ConnectionError("no encontré el teclado Arzopa K81")


def leer() -> tuple[int, int]:
    """(número de efecto 1-14, o 0 si es uno desconocido; brillo 0-5)."""
    h = _abrir()
    try:
        r = list(h.get_feature_report(BUZON, LARGO))
    finally:
        h.close()
    return (EFECTOS.index(r[1]) + 1 if r[1] in EFECTOS else 0), r[2]


def cambiar(efecto: int | None = None, brillo: int | None = None) -> tuple[int, int]:
    """Cambia el efecto (1-14) y/o el brillo (0 = apagado, 5 = máximo). Devuelve cómo quedó."""
    if efecto is not None and not 1 <= efecto <= len(EFECTOS):
        raise ValueError(f"el teclado tiene efectos del 1 al {len(EFECTOS)}")
    if brillo is not None and not 0 <= brillo <= 5:
        raise ValueError("el brillo del teclado va de 0 a 5")
    h = _abrir()
    try:
        r = list(h.get_feature_report(BUZON, LARGO))
        if efecto is not None:
            r[1] = EFECTOS[efecto - 1]
        if brillo is not None:
            r[2] = brillo
        h.send_feature_report(r)
        quedo = list(h.get_feature_report(BUZON, LARGO))
    finally:
        h.close()
    return (EFECTOS.index(quedo[1]) + 1 if quedo[1] in EFECTOS else 0), quedo[2]
