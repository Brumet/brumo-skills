"""Cotizador: lamina un modelo con Bambu Studio (sin abrir ventanas) y calcula el precio
con la MISMA fórmula de la calculadora de la app (una calculadora de costos web).

Bambu Studio por línea de comandos no entiende los perfiles "heredados" (los del usuario solo
guardan lo que cambian), así que aquí se arman completos: se sigue la cadena `inherits` hasta
el perfil base del sistema y se mezclan, igual que hace la app al abrirlos.
"""
from __future__ import annotations

import json
import math
import os
import re
import shutil
import subprocess
import tempfile
import unicodedata
import zipfile
from pathlib import Path

PROGRAMA = Path(os.environ.get("BAMBU_STUDIO", r"C:\Program Files\Bambu Studio\bambu-studio.exe"))
SISTEMA = PROGRAMA.parent / "resources" / "profiles" / "BBL"
DATOS = Path(os.environ.get("APPDATA", "")) / "BambuStudio" / "user"
MAQUINA = "Bambu Lab A1 0.4 nozzle"
FILAMENTO = "Generic PLA @BBL A1"
# Perfil de proceso por defecto: SIEMPRE Gemelas, salvo que el dueño pida otro (pedido del dueño, nov 2026).
PROCESO = "Gemelas"
TIEMPO_MAXIMO = 600  # segundos laminando antes de rendirse
EXTENSIONES = {".3mf", ".stl", ".step", ".stp", ".obj"}

# ---- VALORES DE EJEMPLO: pon aquí tus propios costos (pesos colombianos) ----
COSTO_FILAMENTO_POR_KG = 80000
POTENCIA_WATTS = 225
COSTO_KWH = 800
COSTO_OPERARIO_HORA = 1000
COSTO_ESPACIO_HORA = 200
COSTO_DESGASTE_HORA = 50
COSTO_EMBALAJE = 2000
MARGEN_ERROR = 0.10
MARGEN_GANANCIA = 0.5


def calcular_filamento(gramos: float, horas: float) -> dict:
    """Réplica exacta de calcularFilamento + terminar de js/costeo.js."""
    material = gramos / 1000 * COSTO_FILAMENTO_POR_KG
    energia = horas * POTENCIA_WATTS / 1000 * COSTO_KWH
    desgaste = horas * COSTO_DESGASTE_HORA
    error = (material + energia + desgaste) * MARGEN_ERROR
    operario = horas * COSTO_OPERARIO_HORA
    espacio = horas * COSTO_ESPACIO_HORA
    costo = material + energia + desgaste + error + operario + espacio + COSTO_EMBALAJE
    base = costo - espacio  # el espacio no entra en la base de ganancia (como en el Excel)
    return {"material": material, "energia": energia, "desgaste": desgaste, "error": error,
            "operario": operario, "espacio": espacio, "embalaje": COSTO_EMBALAJE, "costo": costo,
            "sugerido": costo + base * MARGEN_GANANCIA, "minimo": costo * 1.2}


def redondear(valor: float) -> int:
    """Como Math.round de la app (la mitad sube); el round de Python redondea la mitad al par."""
    return math.floor(valor + 0.5)


def entradas_app(gramos: float, horas: float) -> tuple[float, float, str]:
    """Lo que se escribe en la calculadora de la app: gramos con 2 decimales (como los muestra Bambu Studio)
    y horas al minuto ("9:22"). El precio de Brumo se calcula con ESTOS valores, así da idéntico a la app."""
    minutos = redondear(horas * 60)
    return round(gramos, 2), minutos / 60, f"{minutos // 60}:{minutos % 60:02d}"


def _simple(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto.lower())
    return "".join(c for c in texto if c.isalnum())


# ---------------- Perfiles ----------------

def _leer(ruta: Path) -> dict | None:
    try:
        return json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def perfiles(tipo: str, sistema: Path | None = None, usuario: Path | None = None) -> dict[str, Path]:
    """Nombre → archivo de todos los perfiles de un tipo (process, machine, filament)."""
    sistema, usuario = sistema or SISTEMA, usuario or DATOS
    hallados: dict[str, Path] = {}
    carpetas = [sistema / tipo] + sorted(usuario.glob(f"*/{tipo}")) if usuario.exists() else [sistema / tipo]
    for carpeta in carpetas:
        for ruta in carpeta.rglob("*.json"):
            d = _leer(ruta)
            if d and d.get("name"):
                hallados.setdefault(d["name"], ruta) if carpeta == sistema / tipo else hallados.__setitem__(d["name"], ruta)
    return hallados


def buscar(texto: str, nombres) -> str | None:
    """'gemelas' → '0.20mm Standard @BBL A1 - Gemelas'. Prefiere el nombre exacto y luego los del usuario."""
    nombres = list(nombres)
    if texto in nombres:
        return texto
    q = _simple(texto)
    if not q:
        return None
    candidatos = [n for n in nombres if q in _simple(n)]
    return min(candidatos, key=len) if candidatos else None


def aplanar(nombre: str, tipo: str, todos: dict[str, Path]) -> dict:
    """Mezcla el perfil con toda su cadena de padres; el hijo gana."""
    cadena, actual, vistos = [], nombre, set()
    while actual and actual not in vistos:
        vistos.add(actual)
        ruta = todos.get(actual)
        if ruta is None:
            raise ValueError(f"No encuentro el perfil «{actual}».")
        d = _leer(ruta) or {}
        cadena.append(d)
        actual = d.get("inherits", "")
    final: dict = {}
    for d in reversed(cadena):
        final.update(d)
    final.pop("inherits", None)
    final.update({"name": nombre, "from": "system", "instantiation": "true", "type": tipo})
    return final


# ---------------- Laminar ----------------

def leer_resultado(archivo_3mf, placa: int | None = None) -> dict:
    """Tiempo y gramos que Bambu Studio dejó en Metadata/slice_info.config (suma de placas, o solo `placa`),
    con los gramos de cada filamento (tipo y color) para descontarlos del inventario."""
    with zipfile.ZipFile(archivo_3mf) as z:
        info = z.read("Metadata/slice_info.config").decode("utf-8")
    if placa is not None:
        bloques = [b for b in re.findall(r"<plate>.*?</plate>", info, re.S)
                   if re.search(rf'key="index" value="{placa}"', b)]
        info = "".join(bloques) or info
    segundos = sum(float(x) for x in re.findall(r'key="prediction" value="([\d.]+)"', info))
    gramos = sum(float(x) for x in re.findall(r'key="weight" value="([\d.]+)"', info))
    # Algunas versiones dejan el peso de la placa en 0 aunque cada filamento sí trae sus gramos
    gramos = max(gramos, sum(float(x) for x in re.findall(r'used_g="([\d.]+)"', info)))
    placas = len(re.findall(r"<plate>", info))
    filamentos: dict[tuple[str, str], float] = {}
    for f in re.findall(r"<filament [^>]*/>", info):
        tipo = re.search(r'type="([^"]*)"', f)
        color = re.search(r'color="([^"]*)"', f)
        usado = re.search(r'used_g="([\d.]+)"', f)
        if usado:
            llave = (tipo.group(1) if tipo else "", (color.group(1) if color else "").upper())
            filamentos[llave] = filamentos.get(llave, 0) + float(usado.group(1))
    return {"horas": segundos / 3600, "gramos": gramos, "placas": placas,
            "filamentos": [{"tipo": t, "color": c, "gramos": round(g, 2)} for (t, c), g in filamentos.items()]}


def filamentos_por_id(archivo_3mf, placa: int = 1) -> list[dict]:
    """Los filamentos de la placa con su número de ranura en el proyecto (1, 2, ...): para saber qué bandeja del
    AMS va con cada uno al imprimir."""
    with zipfile.ZipFile(archivo_3mf) as z:
        info = z.read("Metadata/slice_info.config").decode("utf-8")
    bloques = [b for b in re.findall(r"<plate>.*?</plate>", info, re.S) if re.search(rf'key="index" value="{placa}"', b)]
    salida = []
    for f in re.findall(r"<filament [^>]*/>", (bloques or [info])[0]):
        datos = dict(re.findall(r'(\w+)="([^"]*)"', f))
        if float(datos.get("used_g") or 0) > 0:
            salida.append({"id": int(datos.get("id", 1)), "tipo": datos.get("type", ""),
                           "color": datos.get("color", "").upper()[:7], "gramos": float(datos["used_g"])})
    return salida


def ajustes_del_proyecto(modelo: Path) -> dict:
    """Los ajustes que el dueño guardó en el proyecto .3mf (vacío si es un STL o no los trae)."""
    if modelo.suffix.lower() != ".3mf":
        return {}
    try:
        with zipfile.ZipFile(modelo) as z:
            return json.loads(z.read("Metadata/project_settings.config"))
    except (OSError, KeyError, ValueError, zipfile.BadZipFile):
        return {}


def laminar(modelo: Path, proceso: str | None = None, filamento: str = FILAMENTO, maquina: str = MAQUINA,
            salida: Path | None = None, _intento: int = 1) -> dict:
    """Lamina como lo haría Bambu Studio, sin inventar nada:
    - Proyecto .3mf: con SUS ajustes (perfil, impresora, filamentos y colores de cada pieza). Si se pide
      otro perfil, solo se cambia el perfil de proceso; filamentos y colores siguen siendo los del proyecto.
    - STL u otro modelo suelto: impresora A1 + el perfil pedido (o Gemelas) + el PLA genérico.
    Sin perfil pedido se usa Gemelas también en los .3mf (solo cambia el de proceso si el proyecto traía otro).
    Los perfiles salen de los archivos de Bambu Studio (los del dueño y los de Bambu Lab que heredan)."""
    if not PROGRAMA.exists():
        raise RuntimeError("No encuentro Bambu Studio instalado.")
    proceso = proceso or PROCESO
    propio = ajustes_del_proyecto(modelo)
    with tempfile.TemporaryDirectory(prefix="brumo-corte-") as carpeta:
        carpeta = Path(carpeta)
        orden = [str(PROGRAMA), "--slice", "0", "--outputdir", str(carpeta), "--export-3mf", "laminado.3mf"]
        perfil = propio.get("print_settings_id") or ""
        filamentos_usados = propio.get("filament_settings_id") or []

        def escribir(tipo: str, pedido: str) -> tuple[str, Path]:
            todos = perfiles(tipo)
            nombre = buscar(pedido, todos)
            if nombre is None:
                raise ValueError(f"No encuentro en Bambu Studio un perfil de {tipo} que se llame «{pedido}».")
            ruta = carpeta / f"{tipo}.json"
            ruta.write_text(json.dumps(aplanar(nombre, tipo, todos)), encoding="utf-8")
            return nombre, ruta

        impresora = propio.get("printer_settings_id") or ""
        otra_impresora = bool(propio) and not es_a1(impresora)
        if otra_impresora:
            # Proyecto hecho para otra impresora (P1S, X1C…): el taller imprime en A1, y Gemelas es un perfil de la A1
            # (Bambu rechaza el perfil de una impresora en otra). Se lamina para la A1; filamentos y colores siguen
            # siendo los del proyecto.
            _, r_maquina = escribir("machine", maquina)
            perfil, r_proceso = escribir("process", proceso)
            orden += ["--load-settings", f"{r_maquina};{r_proceso}"]
        elif propio:
            if proceso and _simple(proceso) not in _simple(perfil):
                perfil, ruta = escribir("process", proceso)
                orden += ["--load-settings", str(ruta)]
        else:
            _, r_maquina = escribir("machine", maquina)
            perfil, r_proceso = escribir("process", proceso)
            nombre_fil, r_fil = escribir("filament", filamento)
            filamentos_usados = [nombre_fil]
            orden += ["--load-settings", f"{r_maquina};{r_proceso}", "--load-filaments", str(r_fil)]
        escala = 1
        if not propio and modelo.suffix.lower() in (".stl", ".obj"):
            modelo_a_laminar, escala = en_milimetros(modelo, carpeta)
        else:
            modelo_a_laminar = modelo
        subprocess.run(orden + [str(modelo_a_laminar)], capture_output=True, timeout=TIEMPO_MAXIMO,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        laminado = carpeta / "laminado.3mf"
        por_placas = None
        if not laminado.exists():
            error = (_leer(carpeta / "result.json") or {}).get("error_string") or "no dejó resultado"
            vacia = re.search(r"fully inside|plate is empty", error, re.I)
            if vacia and propio and salida is None:
                # Proyecto con alguna placa vacía: placa por placa y se suman las que tienen piezas
                por_placas = _por_placas(orden, modelo_a_laminar, carpeta)
            if por_placas is None:
                no_cabe = _no_cabe(modelo_a_laminar) if vacia else None
                raise RuntimeError(no_cabe or f"Bambu Studio no pudo laminar: {error}")
        resultado = por_placas or leer_resultado(laminado)
        if salida is not None:  # el archivo listo para imprimir (trae el G-code de cada placa)
            salida.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(laminado, salida)
            resultado["archivo"] = salida
            resultado["filamentos_id"] = filamentos_por_id(salida)
    resultado.update(perfil=perfil, filamentos_perfil=sorted(set(filamentos_usados)), del_proyecto=bool(propio))
    if otra_impresora:
        resultado["impresora_original"] = impresora
    if escala != 1:
        resultado["escala_unidades"] = escala
    # Bambu a veces ignora la pintura de colores (mismo archivo: 136 g en un color o 205 g en dos). Si el proyecto
    # está pintado y salió de un solo color, se repite (hasta 3 veces) y vale lo mayor: nunca cotizar de menos.
    colores = [f for f in resultado.get("filamentos") or [] if f.get("gramos", 0) > 0.01]
    if propio and len(colores) < 2 and _intento < 3 and es_pintado(modelo):
        otro = laminar(modelo, proceso, filamento, maquina, salida, _intento + 1)
        if otro["gramos"] > resultado["gramos"]:
            return otro
    return resultado


def es_pintado(modelo: Path) -> bool:
    """¿El proyecto trae piezas pintadas a colores?"""
    try:
        with zipfile.ZipFile(modelo) as z:
            for n in z.namelist():
                if n.lower().endswith(".model"):
                    t = z.read(n)
                    if b"paint_color=" in t or b"mmu_segmentation" in t:
                        return True
    except (OSError, zipfile.BadZipFile):
        pass
    return False


def _por_placas(orden: list[str], modelo: Path, carpeta: Path) -> dict | None:
    """Lamina cada placa del proyecto por separado (--slice N) y suma las que tienen piezas."""
    try:
        with zipfile.ZipFile(modelo) as z:
            n = max(1, z.read("Metadata/model_settings.config").decode("utf-8").count("<plate>"))
    except (OSError, KeyError, zipfile.BadZipFile):
        return None
    total = {"horas": 0.0, "gramos": 0.0, "placas": 0, "filamentos": []}
    colores: dict[tuple[str, str], float] = {}
    for k in range(1, n + 1):
        laminado = carpeta / "laminado.3mf"
        laminado.unlink(missing_ok=True)
        cmd = list(orden)
        cmd[cmd.index("--slice") + 1] = str(k)
        subprocess.run(cmd + [str(modelo)], capture_output=True, timeout=TIEMPO_MAXIMO,
                       creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if not laminado.exists():
            continue   # placa vacía
        r = leer_resultado(laminado)
        total["horas"] += r["horas"]
        total["gramos"] += r["gramos"]
        total["placas"] += 1
        for f in r["filamentos"]:
            colores[(f["tipo"], f["color"])] = colores.get((f["tipo"], f["color"]), 0) + f["gramos"]
    if not total["placas"]:
        return None
    total["filamentos"] = [{"tipo": t, "color": c, "gramos": round(g, 2)} for (t, c), g in colores.items()]
    return total


CAMA_A1_MM = 256


def _no_cabe(modelo: Path) -> str | None:
    """Si la pieza es más grande que la cama de la A1, lo dice claro (con medidas y en cuántas partes cortarla)."""
    if modelo.suffix.lower() not in (".stl", ".obj"):
        return None
    try:
        import trimesh
        x, y, z = (float(v) for v in trimesh.load(modelo, force="mesh").extents)
    except Exception:  # noqa: BLE001
        return None
    if max(x, y, z) <= CAMA_A1_MM:
        return None
    partes = max(2, math.ceil(x / 240) * math.ceil(y / 240) * math.ceil(z / 240))
    return (f"No cabe en la cama de la A1: mide {x / 10:.1f} × {y / 10:.1f} × {z / 10:.1f} cm y la cama es de 25,6 cm. "
            f"Hay que cortarlo en unas {partes} partes (en Bambu Studio, tecla C). una app que corte en bloques sí lo cotiza completo "
            "a tamaño real.")


def en_milimetros(modelo: Path, carpeta: Path) -> tuple[Path, int]:
    """Regla de unidades: si la pieza mide menos de 1 está en metros (×1000), si
    mide menos de 10 en centímetros (×10). Sin esto Bambu ve una pieza de medio milímetro y no la lamina.
    Aprovecha para quitar caras repetidas o de área cero (confunden al laminador)."""
    try:
        import trimesh
        malla = trimesh.load(modelo, force="mesh")
        mayor = float(max(malla.extents))
    except Exception:  # noqa: BLE001 - si no se puede leer, que Bambu lo intente tal cual
        return modelo, 1
    escala = 1000 if mayor < 1 else 10 if mayor < 10 else 1
    repetidas = len(malla.faces) - len(malla.unique_faces())
    if escala == 1 and repetidas == 0:
        return modelo, 1
    malla.update_faces(malla.unique_faces())
    malla.update_faces(malla.nondegenerate_faces())
    if escala != 1:
        malla.apply_scale(escala)
    salida = carpeta / f"{modelo.stem}.stl"
    malla.export(salida)
    return salida, escala


def es_a1(impresora: str) -> bool:
    """La A1 (no la A1 mini): la impresora del taller."""
    t = _simple(impresora)
    return "a1" in t and "mini" not in t


# ---------------- Encontrar el archivo ----------------

def carpetas_modelos() -> list[Path]:
    casa = Path.home()
    return [Path("data/modelos"), casa / "Desktop", casa / "Downloads", casa / "Documents",
            casa / "OneDrive" / "Desktop", casa / "OneDrive" / "Documentos"]


def encontrar_modelo(texto: str, carpetas: list[Path] | None = None) -> list[Path]:
    """Busca por nombre (sin tildes ni mayúsculas) en las carpetas de siempre; los más recientes primero."""
    directo = Path(texto.strip('"'))
    if directo.suffix.lower() in EXTENSIONES and directo.exists():
        return [directo]
    q = _simple(Path(texto).stem if Path(texto).suffix.lower() in EXTENSIONES else texto)
    hallados = []
    for carpeta in carpetas or carpetas_modelos():
        if not carpeta.exists():
            continue
        for ruta in carpeta.rglob("*"):
            if ruta.suffix.lower() in EXTENSIONES and q and q in _simple(ruta.stem):
                hallados.append(ruta)
    return sorted(set(hallados), key=lambda r: r.stat().st_mtime, reverse=True)


def abrir_en_bambu(modelo: Path) -> None:
    subprocess.Popen([str(PROGRAMA), str(modelo)])


def horas_texto(horas: float) -> str:
    h, m = divmod(round(horas * 60), 60)
    return f"{h} h {m} min" if h else f"{m} min"
