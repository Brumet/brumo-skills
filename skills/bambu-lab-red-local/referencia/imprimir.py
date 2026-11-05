"""Mandar a imprimir: laminar con Bambu Studio, escoger la bandeja del AMS de cada color, subir el archivo a la
impresora y darle la orden. Es lo mismo que hace Bambu Studio al darle "Imprimir" por la red local.

Seguridad (pedido del dueño: nada sin su "sí"):
- Solo con confirmación (ctx.proponer) y solo si la impresora está conectada y LIBRE (no imprimiendo ni en pausa).
- Si algún color del archivo no está cargado en el AMS, no se manda: se dice qué falta.
- Justo antes de mandar se vuelve a revisar que siga libre.
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

from brumo.impresoras import TRABAJANDO, Impresora, Monitor, _FTPSImplicito
from brumo.negocio.inventario import distancia

CARPETA = Path("data/imprimir")
PARECIDO = 90  # distancia máxima de color para decir que una bandeja "es" ese color


def bandejas(estado: dict[str, Any]) -> list[dict]:
    """Lo cargado en la impresora: [{indice, tipo, color}] (índice global del AMS: ams*4 + bandeja)."""
    salida = []
    for ams in (estado.get("ams") or {}).get("ams") or []:
        for t in ams.get("tray") or []:
            tipo = str(t.get("tray_type") or "").strip()
            if tipo:
                salida.append({"indice": int(ams.get("id", 0)) * 4 + int(t.get("id", 0)), "tipo": tipo,
                               "color": "#" + str(t.get("tray_color") or "000000")[:6].upper()})
    externo = estado.get("vt_tray") or {}
    if str(externo.get("tray_type") or "").strip():
        salida.append({"indice": 254, "tipo": str(externo["tray_type"]).strip(),
                       "color": "#" + str(externo.get("tray_color") or "000000")[:6].upper()})
    return salida


def asignar(filamentos: list[dict], cargado: list[dict], cualquier_color: bool = False
            ) -> tuple[list[int] | None, list[str], list[str]]:
    """(ams_mapping, explicación de cada color, lo que falta). El mapping va por ranura del proyecto (id 1 → [0]).
    cualquier_color=True para STL/OBJ: no traen color (el laminador les pone el verde de Bambu), así que sirve
    cualquier bandeja del mismo material; se prefiere el AMS al carrete externo."""
    mapa: dict[int, int] = {}
    explicacion, falta = [], []
    for f in filamentos:
        mismos = [b for b in cargado if b["tipo"].upper() == f["tipo"].upper()]
        if cualquier_color:
            mejor = min(mismos, key=lambda b: (b["indice"] == 254, b["indice"]), default=None)
        else:
            mejor = min(mismos, key=lambda b: distancia(b["color"], f["color"]), default=None)
        if mejor is None or (not cualquier_color and distancia(mejor["color"], f["color"]) > PARECIDO):
            falta.append(f"{f['tipo']} {f['color']} ({f['gramos']:.0f} g)")
            continue
        mapa[f["id"]] = mejor["indice"]
        donde = "carrete externo" if mejor["indice"] == 254 else f"bandeja {mejor['indice'] % 4 + 1}"
        explicacion.append(f"{f['tipo']} {f['color']} {f['gramos']:.0f} g → {donde} ({mejor['color']})")
    if falta:
        return None, explicacion, falta
    largo = max(mapa) if mapa else 0
    return [mapa.get(i, -1) for i in range(1, largo + 1)], explicacion, []


def nombre_en_impresora(modelo: Path) -> str:
    base = re.sub(r"[^A-Za-z0-9_-]+", "_", modelo.stem).strip("_")[:40] or "brumo"
    return f"{base}.gcode.3mf"


def orden(nombre: str, mapping: list[int], placa: int = 1) -> dict:
    """La misma orden que manda Bambu Studio por la red local (project_file)."""
    usa_ams = any(0 <= i < 254 for i in mapping)
    return {"print": {
        "sequence_id": "0", "command": "project_file", "param": f"Metadata/plate_{placa}.gcode",
        "project_id": "0", "profile_id": "0", "task_id": "0", "subtask_id": "0",
        "subtask_name": nombre.removesuffix(".gcode.3mf"), "file": nombre, "url": f"file:///sdcard/{nombre}",
        "md5": "", "timelapse": False, "bed_type": "auto", "bed_levelling": True, "flow_cali": True,
        "vibration_cali": True, "layer_inspect": False, "use_ams": usa_ams,
        "ams_mapping": mapping if usa_ams else [],
    }}


def subir(imp: Impresora, archivo: Path, nombre: str, espera: float = 60) -> None:
    """Copia el archivo a la tarjeta de la impresora (FTPS, puerto 990), como Bambu Studio."""
    import ssl

    contexto = ssl.create_default_context()
    contexto.check_hostname = False
    contexto.verify_mode = ssl.CERT_NONE  # certificado propio de la impresora; tráfico dentro de la red local
    ftp = _FTPSImplicito(context=contexto, timeout=espera)
    ftp.connect(imp.ip, 990)
    try:
        ftp.login("bblp", imp.codigo)
        ftp.prot_p()
        ftp.storbinary(f"STOR /{nombre}", io.BytesIO(archivo.read_bytes()))
    finally:
        try:
            ftp.quit()
        except Exception:  # noqa: BLE001
            pass


def libre(monitor: Monitor, imp: Impresora) -> str | None:
    """None si se puede imprimir; si no, por qué."""
    if not imp.ip or imp.nombre not in monitor.conectadas:
        return f"{imp.nombre} no está conectada (¿apagada?)."
    estado = str((monitor.estados.get(imp.nombre) or {}).get("gcode_state") or "").upper()
    if estado in TRABAJANDO:
        return f"{imp.nombre} está ocupada ({estado.lower()})."
    return None
