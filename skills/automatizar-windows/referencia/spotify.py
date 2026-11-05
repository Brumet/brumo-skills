"""Controlar Spotify.

Dos niveles:
- Sin configurar nada: pausar/seguir, siguiente, anterior y volumen con las teclas multimedia de Windows
  (sirven para Spotify de escritorio o cualquier reproductor abierto en este PC).
- Sin cuenta ni Premium (lo que hay): poner una canción por su nombre en la app de Spotify del PC (poner_en_app).
- Con la cuenta conectada (setup/spotify_conectar.py, una vez) y Premium: lo mismo en cualquier aparato donde Spotify
  esté abierto (PC, celular, parlante). Spotify exige Premium para manejar la reproducción desde otro programa.

Solo se le manda a Spotify lo que hay que buscar ("rock en español"); ningún dato del negocio.
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path

import httpx

from brumo.config import RAIZ

API = "https://api.spotify.com/v1"
TOKEN = "https://accounts.spotify.com/api/token"
ARCHIVO = RAIZ / "data" / "spotify.json"  # data/ nunca se sube a GitHub
PERMISOS = "user-modify-playback-state user-read-playback-state user-read-currently-playing"

TECLAS = {"pausa": 0xB3, "seguir": 0xB3, "siguiente": 0xB0, "anterior": 0xB1, "subir": 0xAF, "bajar": 0xAE}


def tecla_multimedia(accion: str, veces: int = 1) -> bool:
    if sys.platform != "win32" or accion not in TECLAS:
        return False
    import ctypes

    for _ in range(veces):
        ctypes.windll.user32.keybd_event(TECLAS[accion], 0, 0, 0)
        ctypes.windll.user32.keybd_event(TECLAS[accion], 0, 2, 0)  # soltar
    return True


# ---------------------------------------------------------------------------------------------------------------
# Sin cuenta conectada ni Premium (lo que tiene el dueño): la app de escritorio de Spotify.
# Se probó (nov 2026): abrir "spotify:track:<id>" en la app la pone a sonar sola, también con cuenta gratis. La
# canción se busca en internet (open.spotify.com/track/…), sin la API de Spotify. La app ya está abierta (el dueño la
# usa), así que no gasta nada extra; un navegador con el reproductor web serían ~400 MB más. Después se vuelve a
# minimizar para que no aparezca nada en la pantalla.
# ---------------------------------------------------------------------------------------------------------------
TITULOS_QUIETOS = ("spotify", "spotify free", "spotify premium")  # el título de la ventana cuando no suena nada
PISTA = re.compile(r"open\.spotify\.com/(?:intl-[a-z]+/)?track/([A-Za-z0-9]{22})")


def _ventana_spotify() -> tuple[int, str] | None:
    """(ventana, título) de la app de Spotify, o None si no está abierta."""
    if sys.platform != "win32":
        return None
    import ctypes
    from ctypes import wintypes

    u, k = ctypes.windll.user32, ctypes.windll.kernel32
    hallada: list[tuple[int, str]] = []

    def revisar(h, _):
        largo = u.GetWindowTextLengthW(h)
        if not largo or not u.IsWindowVisible(h):
            return True
        pid = wintypes.DWORD()
        u.GetWindowThreadProcessId(h, ctypes.byref(pid))
        proceso = k.OpenProcess(0x1000, False, pid.value)  # solo leer el nombre del programa
        if proceso:
            ruta = ctypes.create_unicode_buffer(260)
            n = wintypes.DWORD(260)
            k.QueryFullProcessImageNameW(proceso, 0, ruta, ctypes.byref(n))
            k.CloseHandle(proceso)
            if ruta.value.lower().endswith("spotify.exe"):
                titulo = ctypes.create_unicode_buffer(largo + 1)
                u.GetWindowTextW(h, titulo, largo + 1)
                hallada.append((h, titulo.value))
                return False
        return True

    u.EnumWindows(ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)(revisar), 0)
    return hallada[0] if hallada else None


def sonando_en_app() -> str | None:
    """Lo que suena en la app ("Artista - Canción"), o None si no suena nada o no está abierta."""
    v = _ventana_spotify()
    return v[1] if v and v[1].strip().lower() not in TITULOS_QUIETOS else None


def buscar_pista(busqueda: str) -> str | None:
    """El id de la canción en Spotify, buscando en internet (solo se manda lo que hay que buscar)."""
    from ddgs import DDGS

    for buscador in ("auto", "bing", "brave"):
        try:
            resultados = DDGS(timeout=8).text(f"site:open.spotify.com/track {busqueda}", max_results=6,
                                              backend=buscador)
        except Exception:  # noqa: BLE001 - se prueba con el siguiente buscador
            continue
        for r in resultados:
            m = PISTA.search(r.get("href", ""))
            if m:
                return m.group(1)
    return None


def poner_en_app(busqueda: str, esperar: float = 8.0) -> str:
    import os

    if sys.platform != "win32":
        return "Solo puedo poner música en la app de Spotify del PC con Windows."
    pista = buscar_pista(busqueda)
    if pista is None:
        return f"No encontré '{busqueda}' en Spotify."
    import ctypes

    u = ctypes.windll.user32
    antes = _ventana_spotify()
    if antes is None:  # abrirla y esperar a que esté lista: a una app que apenas arranca no le llega la canción
        os.startfile("spotify:")
        for _ in range(40):
            time.sleep(0.5)
            antes = _ventana_spotify()
            if antes:
                break
        time.sleep(2)
        minimizar = True
    else:
        minimizar = bool(u.IsIconic(antes[0]))
    delante = u.GetForegroundWindow()
    os.startfile(f"spotify:track:{pista}")
    titulo = None
    for _ in range(int(esperar / 0.5)):
        time.sleep(0.5)
        titulo = sonando_en_app()
        if titulo:
            break
    # Abrir la canción trae la app al frente: se deja como estaba (minimizada, o detrás de lo que se estaba usando).
    v = _ventana_spotify()
    if v and minimizar:
        u.ShowWindow(v[0], 6)  # minimizar
    elif delante and v and delante != v[0]:
        u.SetForegroundWindow(delante)
    if not titulo:
        return "Abrí la canción en Spotify, pero no arrancó sola. Dale play en la app."
    return f"▶️ Sonando: {titulo.replace(' - ', ' — ', 1)}"


class Spotify:
    def __init__(self, archivo: Path = ARCHIVO):
        self.archivo = archivo
        self.datos = json.loads(archivo.read_text(encoding="utf-8")) if archivo.exists() else {}

    @property
    def conectado(self) -> bool:
        return bool(self.datos.get("refresh_token") and self.datos.get("client_id"))

    def _token(self) -> str:
        if self.datos.get("access_token") and time.time() < self.datos.get("vence", 0) - 60:
            return self.datos["access_token"]
        r = httpx.post(TOKEN, timeout=15, data={"grant_type": "refresh_token", "client_id": self.datos["client_id"],
                                                 "refresh_token": self.datos["refresh_token"]})
        r.raise_for_status()
        nuevo = r.json()
        self.datos.update(access_token=nuevo["access_token"], vence=time.time() + nuevo.get("expires_in", 3600))
        if nuevo.get("refresh_token"):
            self.datos["refresh_token"] = nuevo["refresh_token"]
        self.archivo.write_text(json.dumps(self.datos), encoding="utf-8")
        return self.datos["access_token"]

    def _pedir(self, metodo: str, ruta: str, **kwargs) -> httpx.Response:
        r = httpx.request(metodo, f"{API}{ruta}", timeout=15, headers={"Authorization": f"Bearer {self._token()}"}, **kwargs)
        if r.status_code == 403:
            raise RuntimeError("Spotify no deja controlar la música: hace falta una cuenta Premium.")
        if r.status_code == 404:
            raise RuntimeError("No hay ningún Spotify abierto. Abre Spotify en el PC o el celular y repite.")
        r.raise_for_status()
        return r

    def _aparato(self) -> str | None:
        aparatos = self._pedir("GET", "/me/player/devices").json().get("devices") or []
        if not aparatos:
            return None
        activo = next((a for a in aparatos if a.get("is_active")), aparatos[0])
        return activo["id"]

    def reproducir(self, busqueda: str, tipo: str = "cancion") -> str:
        tipos = {"cancion": "track", "artista": "artist", "album": "album", "playlist": "playlist"}
        t = tipos.get(tipo, "track")
        resultados = self._pedir("GET", "/search", params={"q": busqueda, "type": t, "limit": 1, "market": "CO"}).json()
        encontrados = (resultados.get(f"{t}s") or {}).get("items") or []
        encontrados = [e for e in encontrados if e]
        if not encontrados:
            return f"No encontré '{busqueda}' en Spotify."
        e = encontrados[0]
        aparato = self._aparato()
        if aparato is None:
            raise RuntimeError("No hay ningún Spotify abierto. Abre Spotify en el PC o el celular y repite.")
        cuerpo = {"uris": [e["uri"]]} if t == "track" else {"context_uri": e["uri"]}
        self._pedir("PUT", "/me/player/play", params={"device_id": aparato}, json=cuerpo)
        quien = ", ".join(a["name"] for a in e.get("artists") or [])
        return f"▶️ Sonando: {e['name']}" + (f" — {quien}" if quien else "")

    def accion(self, accion: str) -> None:
        rutas = {"pausa": ("PUT", "/me/player/pause"), "seguir": ("PUT", "/me/player/play"),
                 "siguiente": ("POST", "/me/player/next"), "anterior": ("POST", "/me/player/previous")}
        metodo, ruta = rutas[accion]
        self._pedir(metodo, ruta)

    def volumen(self, porcentaje: int) -> None:
        self._pedir("PUT", "/me/player/volume", params={"volume_percent": max(0, min(100, int(porcentaje)))})

    def que_suena(self) -> str:
        r = self._pedir("GET", "/me/player/currently-playing")
        if r.status_code == 204 or not r.content:
            return "No está sonando nada."
        item = r.json().get("item") or {}
        quien = ", ".join(a["name"] for a in item.get("artists") or [])
        return f"Está sonando {item.get('name', 'algo')}" + (f" de {quien}" if quien else "") + "."
