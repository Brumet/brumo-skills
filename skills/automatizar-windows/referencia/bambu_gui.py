"""Mandar a imprimir A TRAVÉS de Bambu Studio (pedido del dueño: "solo con decirle imprime tal archivo, lo imprima").

Por qué así: desde 2025 el firmware de Bambu ("Authorization Control") solo deja ARRANCAR impresiones a sus programas;
la orden directa por la red local se ignora. Bambu Studio sí puede, así que Brumo lo maneja como lo haría una persona:
1. Elige la impresora escribiendo `app.user_last_selected_machine` en BambuStudio.conf (con su firma MD5).
2. Abre el archivo ya laminado (con Gemelas) en una ventana nueva de Bambu Studio.
3. Lamina (Ctrl+R), oprime "Imprimir placa" y en la ventana de envío oprime "Enviar".
   Los botones son dibujados a mano (no se pueden buscar por nombre), así que se encuentran por su color verde Bambu.
4. Cierra esa ventana de Bambu Studio.
Necesita la sesión de Windows abierta (no sirve con la pantalla bloqueada) y no funciona en el mini PC sin escritorio.
"""
from __future__ import annotations

import ctypes
import hashlib
import logging
import os
import re
import subprocess
import time
from ctypes import wintypes
from pathlib import Path

log = logging.getLogger(__name__)

CONF = Path(os.environ.get("APPDATA", "")) / "BambuStudio" / "BambuStudio.conf"



# ---------------------------------------------------------------- configuración: qué impresora queda elegida
def firmar(cuerpo: str) -> str:
    """Bambu Studio firma el archivo con el MD5 del JSON con saltos de línea \\n y sin el salto final."""
    return hashlib.md5(cuerpo.replace("\r\n", "\n").rstrip("\n").encode("utf-8")).hexdigest().upper()


def elegir_impresora(serie: str, conf: Path = CONF) -> None:
    crudo = conf.read_bytes().decode("utf-8")
    i = crudo.rfind("# MD5 checksum")
    cuerpo = crudo[:i] if i >= 0 else crudo
    nuevo, n = re.subn(r'("user_last_selected_machine"\s*:\s*")[^"]*(")', rf"\g<1>{serie}\g<2>", cuerpo, count=1)
    if n == 0:
        nuevo = re.sub(r'("app"\s*:\s*\{)', rf'\1\r\n        "user_last_selected_machine": "{serie}",', cuerpo, count=1)
    if nuevo == cuerpo:
        return
    salto = "\r\n" if "\r\n" in cuerpo else "\n"
    respaldo = conf.with_suffix(".conf.brumo")
    respaldo.write_bytes(crudo.encode("utf-8"))
    conf.write_bytes((nuevo + f"# MD5 checksum {firmar(nuevo)}{salto}").encode("utf-8"))




# ---------------------------------------------------------------- ventanas, captura y clics (Win32)
# Para ver los botones se "fotografía" la ventana aunque otra la tape (PrintWindow). Para oprimirlos sí hace falta
# traerla al frente un momento y usar el mouse (los botones de Bambu no responden a clics por mensajes); al final se
# devuelve el foco a la ventana que el dueño estaba usando.
def _u32():
    u = ctypes.windll.user32
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)  # coordenadas reales de pantalla
    except Exception:  # noqa: BLE001 - ya estaba puesto
        pass
    return u


def ventanas(pid: int) -> list[tuple[int, str]]:
    u = _u32()
    halladas: list[tuple[int, str]] = []

    def revisar(h, _):
        dueño = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(dueño))
        if dueño.value == pid and u.IsWindowVisible(h):
            largo = u.GetWindowTextLengthW(h)
            titulo = ctypes.create_unicode_buffer(largo + 1)
            u.GetWindowTextW(h, titulo, largo + 1)
            halladas.append((h, titulo.value))
        return True

    u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(revisar), 0)
    return halladas


def rect(h: int) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    _u32().GetWindowRect(h, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def captura(h: int):
    """Imagen (PIL) de la ventana completa, aunque esté tapada por otras."""
    from PIL import Image

    u, g = _u32(), ctypes.windll.gdi32
    x0, y0, x1, y1 = rect(h)
    ancho, alto = x1 - x0, y1 - y0
    dc = u.GetWindowDC(h)
    mem = g.CreateCompatibleDC(dc)
    bmp = g.CreateCompatibleBitmap(dc, ancho, alto)
    g.SelectObject(mem, bmp)
    u.PrintWindow(h, mem, 2)  # PW_RENDERFULLCONTENT

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                    ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                    ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                    ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]

    info = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), ancho, -alto, 1, 32, 0, 0, 0, 0, 0, 0)
    datos = ctypes.create_string_buffer(ancho * alto * 4)
    g.GetDIBits(mem, bmp, 0, alto, datos, ctypes.byref(info), 0)
    g.DeleteObject(bmp)
    g.DeleteDC(mem)
    u.ReleaseDC(h, dc)
    return Image.frombuffer("RGB", (ancho, alto), datos, "raw", "BGRX", 0, 1)


def botones_verdes(h: int, caja: tuple[int, int, int, int] | None = None, minimo: int = 40) -> list[tuple[int, int, int]]:
    """(x, y, ancho) relativos a la ventana de las manchas verdes de botón activo, de izquierda a derecha.
    caja = (x0, y0, x1, y1) relativa a la ventana; números negativos se cuentan desde el borde derecho / de abajo."""
    import numpy as np

    img = captura(h)
    ancho, alto = img.size
    if caja:
        x0, y0, x1, y1 = (v if v >= 0 else lado + v for v, lado in zip(caja, (ancho, alto, ancho, alto)))
    else:
        x0, y0, x1, y1 = 0, 0, ancho, alto
    px = np.asarray(img.crop((x0, y0, x1, y1))).astype(int)
    r, g, b = px[..., 0], px[..., 1], px[..., 2]
    # Verde "de botón activo": Bambu usa varios tonos (#00AE42 y #3DCB73); los botones apagados son grises.
    verde = (g > 150) & (g - r > 90) & (g - b > 50)
    columnas = verde.sum(axis=0) >= 6  # columnas con varios píxeles verdes = parte de un botón
    manchas, inicio = [], None
    for x, hay in enumerate(list(columnas) + [False]):
        if hay and inicio is None:
            inicio = x
        elif not hay and inicio is not None:
            if x - inicio >= minimo:
                filas = np.where(verde[:, inicio:x].any(axis=1))[0]
                manchas.append((x0 + (inicio + x) // 2, y0 + int(filas.mean()), x - inicio))
            inicio = None
    return manchas


def al_frente(h: int) -> None:
    """Trae la ventana al frente aunque Brumo corra de fondo: se "pega" un momento a la entrada de la ventana que tiene
    el foco (AttachThreadInput), que es lo que Windows exige para dejar cambiar el foco."""
    u, k = _u32(), ctypes.windll.kernel32
    actual = u.GetForegroundWindow()
    u.SetWindowPos(h, -1, 0, 0, 0, 0, 0x0001 | 0x0002)  # siempre encima (ver abajo)
    if actual == h:
        return
    hilo = u.GetWindowThreadProcessId(actual, None)
    mio = k.GetCurrentThreadId()
    u.AttachThreadInput(mio, hilo, True)
    u.ShowWindow(h, 5)
    # "Siempre encima" mientras Brumo la usa: si otra ventana lo está (pasó con la app de Claude), taparía los botones.
    # La ventana de Bambu que abre Brumo se cierra al terminar, así que no queda encima de nada.
    u.SetWindowPos(h, -1, 0, 0, 0, 0, 0x0001 | 0x0002)
    u.BringWindowToTop(h)
    u.SetForegroundWindow(h)
    u.AttachThreadInput(mio, hilo, False)
    time.sleep(0.4)


def clic(h: int, x: int, y: int) -> None:
    """Clic de verdad en (x, y) relativo a la ventana h. Los botones de Bambu Studio no hacen caso a clics mandados
    como mensajes (probado), así que se trae la ventana al frente y se usa el mouse; luego el mouse vuelve a su sitio."""
    u = _u32()
    al_frente(h)
    x0, y0, _, _ = rect(h)
    antes = wintypes.POINT()
    u.GetCursorPos(ctypes.byref(antes))
    u.SetCursorPos(x0 + x, y0 + y)
    time.sleep(0.15)
    u.mouse_event(0x0002, 0, 0, 0, 0)
    time.sleep(0.08)
    u.mouse_event(0x0004, 0, 0, 0, 0)
    time.sleep(0.15)
    u.SetCursorPos(antes.x, antes.y)


def estorbos(pid: int) -> list[tuple[int, int, int, int]]:
    """Ventanas de otros programas que están "siempre encima" y reciben clics (taparían los botones de Bambu)."""
    u = _u32()
    cajas: list[tuple[int, int, int, int]] = []

    def revisar(h, _):
        dueño = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(dueño))
        ex = u.GetWindowLongW(h, -20)
        if dueño.value != pid and u.IsWindowVisible(h) and ex & 0x8 and not ex & 0x20:  # TOPMOST y no TRANSPARENT
            cajas.append(rect(h))
        return True

    u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(revisar), 0)
    return cajas


def _choca(a, b) -> bool:
    return a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3]


def acomodar(h: int, pid: int) -> None:
    """Pone la ventana donde sus botones (arriba a la derecha y el centro, donde sale la ventana de envío) no queden
    debajo de otra ventana "siempre encima" (pasó con la ventanita de Claude arriba a la derecha)."""
    u = _u32()
    w, hh = u.GetSystemMetrics(0), u.GetSystemMetrics(1)
    obst = estorbos(pid)
    if not obst:
        return
    opciones = [(0, 0, w, hh)] + [(x, y, x + int(w * f), y + int(hh * g))
                                  for f, g in ((0.75, 0.9), (0.6, 0.8)) for x in (0, w - int(w * f))
                                  for y in (0, hh - int(hh * g))]
    for x0, y0, x1, y1 in opciones:
        zonas = [(x1 - 600, y0, x1, y0 + 90), ((x0 + x1) // 2 - 450, (y0 + y1) // 2 - 350,
                                               (x0 + x1) // 2 + 450, (y0 + y1) // 2 + 350)]
        if not any(_choca(z, o) for z in zonas for o in obst):
            u.ShowWindow(h, 9)  # quitar el maximizado para poder moverla
            u.MoveWindow(h, x0, y0, x1 - x0, y1 - y0, True)
            time.sleep(1.5)
            return


def esperar(condicion, segundos: float, paso: float = 0.5):
    limite = time.time() + segundos
    while time.time() < limite:
        valor = condicion()
        if valor:
            return valor
        time.sleep(paso)
    return None


# ---------------------------------------------------------------- todo junto
def imprimir(archivo: Path, serie: str, probar: bool = False, programa: Path | None = None) -> str:
    """Manda `archivo` (.3mf o .gcode.3mf) a la impresora `serie` con Bambu Studio. Devuelve "" si lo envió, o el
    problema. Con probar=True llega hasta la ventana de envío pero NO oprime Enviar (para revisar)."""
    from brumo.negocio.cotizador import PROGRAMA

    elegir_impresora(serie)
    usaba = _u32().GetForegroundWindow()
    proceso = subprocess.Popen([str(programa or PROGRAMA), str(archivo)])
    try:
        principal = esperar(lambda: next((v for v in ventanas(proceso.pid) if "BambuStudio" in v[1]), None), 120, 1)
        if not principal:
            return "Bambu Studio no abrió."
        h = principal[0]

        def cargado():
            # Mientras carga pueden salir avisos (ej. "Preajuste personalizado") que la frenan: se acepta su botón
            # verde (el de más a la derecha; el de la izquierda es el ícono). Listo = sin ventanas aparte.
            otras = [v for v in ventanas(proceso.pid) if v[0] != h]
            for v in otras:
                if "carg" in v[1].lower() or "load" in v[1].lower():
                    continue
                verdes = botones_verdes(v[0], minimo=20)
                if verdes:
                    clic(v[0], *verdes[-1][:2])
                    time.sleep(1)
            return not otras

        time.sleep(3)
        if not esperar(cargado, 120, 1.5):
            return "Bambu Studio no terminó de abrir el archivo."
        time.sleep(2)
        acomodar(h, proceso.pid)
        # Barra de arriba a la derecha: "Laminar placa" (verde) e "Imprimir placa" (gris hasta laminar).
        barra = (-560, 8, -8, 75)
        laminar = esperar(lambda: (botones_verdes(h, barra) or [None])[-1], 20, 1)
        if not laminar:
            return "No encontré el botón Laminar placa en Bambu Studio."
        clic(h, laminar[0], laminar[1])

        def boton_imprimir():
            manchas = [m for m in botones_verdes(h, barra) if m[0] > laminar[0] + 40]
            return manchas[-1] if manchas else None

        boton = esperar(boton_imprimir, 240, 1)
        if not boton:
            return "Bambu Studio no terminó de laminar."
        time.sleep(1)
        clic(h, boton[0], boton[1])
        envio = esperar(lambda: next((v for v in ventanas(proceso.pid) if v[0] != h and v[1]), None), 40, 0.5)
        if not envio:
            return "No apareció la ventana de envío de Bambu Studio."
        time.sleep(4)  # que cargue la impresora y el AMS en la ventana
        enviar = esperar(lambda: (botones_verdes(envio[0], (-260, -120, -8, -8)) or [None])[-1], 20, 1)
        if not enviar:
            return "No encontré el botón Enviar en Bambu Studio."
        if probar:
            return ""
        clic(envio[0], enviar[0], enviar[1])
        time.sleep(15)  # que suba el archivo antes de cerrar
        return ""
    finally:
        if not probar:
            proceso.kill()
        if usaba:
            try:
                if probar:  # en ensayo la ventana queda abierta: que no siga "siempre encima"
                    for v in ventanas(proceso.pid):
                        _u32().SetWindowPos(v[0], -2, 0, 0, 0, 0, 0x0001 | 0x0002)
                al_frente(usaba)
            except Exception:  # noqa: BLE001 - devolver el foco es cortesía; nunca debe tumbar la impresión
                pass
