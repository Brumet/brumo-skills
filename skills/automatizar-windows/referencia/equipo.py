"""El PC: abrir programas y apagarlo (ahora o cuando Brumo termine lo que está haciendo).

SEGURIDAD (pedido del dueño: "que toque varias cosas, pero que a la larga no se me vaya a dañar"):
- Abrir un programa no pide confirmación, pero solo los del menú Inicio y NUNCA los que pueden dañar Windows
  (registro, consola, PowerShell, servicios, particiones, desinstaladores… ver BLOQUEADOS).
- Apagar SIEMPRE pide "sí" y avisa con 60 s de margen (se cancela con "cancela el apagado").
- Brumo no borra archivos, no corre comandos libres, no instala programas ni cambia ajustes de Windows.
"""
from __future__ import annotations

import logging
import re
import subprocess
import sys
import threading
import time

from brumo.negocio.pedidos import normalizar
from brumo.tools import Contexto, herramienta

log = logging.getLogger(__name__)

# Nada de esto se abre por pedido a Brumo, aunque esté en el menú Inicio.
BLOQUEADOS = ("registro", "regedit", "simbolo del sistema", "cmd", "powershell", "terminal", "servicios",
              "administracion de equipos", "administrador de tareas", "directiva", "programador de tareas",
              "desfragmentar", "liberador", "unidad de recuperacion", "diagnostico", "desinstal", "uninstall",
              "editor del registro", "orígenes de datos", "origenes de datos", "iscsi", "visor de eventos",
              "seguridad de windows", "configuracion del sistema", "componentes", "installer", "developer",
              "command prompt", "native tools", "git bash", "git cmd", "ejecutar")
# Trabajos de Brumo que corren de fondo: "apágalo cuando termines" espera a que acaben.
TRABAJOS = ("claude", "cotizar")


def programas() -> dict[str, str]:
    """Nombre visible → identificador para abrirlo, de TODO el menú Inicio (también las apps de la Tienda, como Paint
    o Calculadora), sin los bloqueados. Sale de Get-StartApps de Windows."""
    salida = subprocess.run(["powershell", "-NoProfile", "-Command",
                             "Get-StartApps | ForEach-Object { $_.Name + [char]9 + $_.AppID }"],
                            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=30).stdout
    hallados: dict[str, str] = {}
    for linea in salida.splitlines():
        nombre, _, app = linea.partition("\t")
        if app and not any(b in normalizar(nombre) for b in BLOQUEADOS):
            hallados.setdefault(nombre.strip(), app.strip())
    return hallados


def buscar_programa(pedido: str, lista: dict[str, str]) -> str | None:
    q = normalizar(pedido).strip()
    if not q:
        return None
    nombres = {normalizar(n): n for n in lista}
    if q in nombres:
        return nombres[q]
    candidatos = [n for k, n in nombres.items() if all(p in k for p in q.split())]
    if not candidatos:
        return None
    # "chrome" → "Google Chrome" (el nombre más corto, no "Escritorio remoto de Chrome");
    # "photoshop" → "Adobe Photoshop 2026" (con el mismo nombre, la versión más nueva).
    sin_numeros = lambda n: "".join(c for c in n if not c.isdigit()).strip()  # noqa: E731
    return min(candidatos, key=lambda n: (len(sin_numeros(n)), [-int(x) for x in re.findall(r"\d+", n)] or [0]))


def bloqueado(pedido: str) -> bool:
    return any(b in normalizar(pedido) for b in BLOQUEADOS)


@herramienta(
    "abrir_programa",
    "Abre un programa del PC por su nombre (ej. 'abre Bambu Studio', 'abre Photoshop', 'abre Discord'). Solo programas "
    "normales del menú Inicio; los del sistema (registro, consola, etc.) no se abren por seguridad.",
    {"programa": {"type": "string", "description": "Nombre del programa"}},
    ["programa"],
)
def abrir_programa(ctx: Contexto, programa: str) -> str:  # noqa: ARG001
    if sys.platform != "win32":
        return "Solo puedo abrir programas en el PC con Windows."
    if bloqueado(programa):
        return f"Por seguridad no abro «{programa}»: es una herramienta del sistema que podría dañar Windows."
    lista = programas()
    nombre = buscar_programa(programa, lista)
    if nombre is None:
        return f"No encontré «{programa}» en el menú Inicio."
    # Igual que al hacer clic en el menú Inicio (sirve para programas y apps de la Tienda)
    subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + lista[nombre]])  # noqa: S603,S607
    log.info("Abrí el programa %s", nombre)
    return f"🚀 Abrí {nombre}."


def trabajos_pendientes() -> list[str]:
    return [h.name for h in threading.enumerate() if h.is_alive() and h.name.startswith(TRABAJOS)]


def apagar(segundos: int = 60) -> None:
    subprocess.run(["shutdown", "/s", "/t", str(segundos), "/c",
                    "Brumo va a apagar el equipo. Para cancelar dile 'cancela el apagado'."], check=False)


@herramienta(
    "apagar_equipo",
    "Apaga el PC (con confirmación). cuando='ahora' (con 1 minuto de aviso) o 'al_terminar' (espera a que Brumo "
    "termine lo que está haciendo de fondo, como un análisis de Claude o una cotización, y luego apaga). Para "
    "deshacerlo: cancelar=true ('cancela el apagado').",
    {"cuando": {"type": "string", "enum": ["ahora", "al_terminar"]},
     "cancelar": {"type": "boolean", "description": "true para cancelar un apagado ya programado"}},
    [],
)
def apagar_equipo(ctx: Contexto, cuando: str = "ahora", cancelar: bool | None = None) -> str:
    if sys.platform != "win32":
        return "Solo puedo apagar el PC con Windows."
    if cancelar or "cancel" in normalizar(ctx.texto):
        subprocess.run(["shutdown", "/a"], check=False)
        _ESPERA.set()
        return "✋ Listo, cancelé el apagado."
    pendientes = trabajos_pendientes()
    if cuando == "al_terminar" or "termin" in normalizar(ctx.texto):
        resumen = ("Apagar el PC cuando termine lo que estoy haciendo" +
                   (f" (ahora: {', '.join(pendientes)})" if pendientes else " (ahora no hay nada pendiente: será en 1 minuto)"))

        def hacer() -> str:
            _ESPERA.clear()

            def esperar_y_apagar():
                while trabajos_pendientes() and not _ESPERA.is_set():
                    time.sleep(15)
                if not _ESPERA.is_set():
                    apagar(60)

            threading.Thread(target=esperar_y_apagar, name="apagar", daemon=True).start()
            return "🌙 Listo: apago el PC cuando termine (con 1 minuto de aviso). Para cancelar: 'cancela el apagado'."

        return ctx.proponer(resumen, hacer)

    def hacer_ya() -> str:
        apagar(60)
        return "🌙 El PC se apaga en 1 minuto. Para cancelar: 'cancela el apagado'."

    aviso = f" OJO: todavía estoy trabajando en {', '.join(pendientes)}." if pendientes else ""
    return ctx.proponer(f"Apagar el PC en 1 minuto.{aviso}", hacer_ya)


_ESPERA = threading.Event()  # se activa al cancelar: el apagado "al terminar" ya no ocurre
