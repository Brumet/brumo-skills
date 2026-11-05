"""Qué herramientas se le muestran a la IA en cada mensaje.

Mandarle las 35 herramientas siempre ocupa ~5.700 tokens que la IA tiene que leer. En la tarjeta de video casi no se
nota, pero en el mini PC (solo procesador) leer eso tarda ~2 minutos en frío. Y con menos opciones el modelo pequeño
se equivoca menos de herramienta.

Cómo se elige (sin IA, por palabras, para que sea instantáneo):
- El NÚCLEO va siempre (agenda, memoria, internet, clima).
- Cada GRUPO entra si el mensaje tiene una de sus palabras, o si ya se usó en esta conversación (para que "sí, y el
  otro" siga teniendo sus herramientas).
- Si un mensaje largo no coincide con nada, van todas: es mejor gastar un poco más que quedarse sin poder hacerlo.

El orden importa: Ollama reutiliza lo ya leído mientras el comienzo no cambie, así que el núcleo va primero y los grupos
se agregan al final en el orden en que entraron a la conversación.
"""
from __future__ import annotations

import re

from brumo.negocio.pedidos import normalizar

NUCLEO = ["listar_pendientes", "agregar_pendiente", "completar_pendiente", "crear_recordatorio",
          "cancelar_recordatorio", "listar_recordatorios", "recordar_dato", "buscar_en_memoria", "olvidar_dato",
          "buscar_internet", "leer_pagina", "clima"]

# grupo: (herramientas, palabras). Las palabras se buscan sin tildes y en minúscula, como pedazos de palabra.
GRUPOS: dict[str, tuple[list[str], list[str]]] = {
    "pedidos": (
        ["consultar_pedido", "crear_pedido", "cambiar_estado_pedido", "cola_fabricacion", "borrador_retraso",
         "imprimir_guia", "recoger_pedidos_web", "resumen_negocio"],
        ["pedido", "ped-", "venta", "vend", "cliente", "cobr", "debe", "deben", "saldo", "abon", "pago", "pagó",
         "pagaron", "entreg", "envio", "envia", "guia", "domicil", "fabrica", "cola", "atrasad", "encarg", "compro",
         "web", "tienda", "contra entrega", "factur", "negocio", "semana", "como me fue", "ingres",
         "ganancia", "mes", "este mes", "estado", "listo para", "termine el", "ya quedo", "whatsapp", "retraso"]),
    "gastos": (
        ["registrar_gasto", "resumen_negocio"],
        ["gast", "compre", "pague", "recibo", "factura", "retiro", "costo", "costó", "invert", "egreso"]),
    "impresion": (
        ["estado_impresoras", "foto_impresora", "enviar_a_imprimir", "orientar_modelo", "cotizar_modelo", "crear_figura"],
        ["impresora", "imprim", "imprime", "impresion", "camara", "foto de la",
         "cama", "boquilla", "capa", "cuanto falta", "progreso", "lamin", "orient", "cotiz", "cuanto cobro",
         "cuanto cobrar", "cuanto cuesta", "cuanto vale", "precio", ".3mf", ".stl", "archivo", "pieza", "modelo",
         "gemelas", "perfil", "bambu", "a1", "ams", "termino", "terminó", "falla", "espagueti", "figura", "disena",
         "diseña", "inventa"]),
    "filamento": (
        ["inventario_filamento", "registrar_rollo", "gastar_filamento"],
        ["filamento", "rollo", "carrete", "bobina", "pla", "petg", "tpu", "abs", "gramos", "material"]),
    "casa": (
        ["luces", "teclado", "musica"],
        ["luz", "luces", "lampara", "led", "rgb", "teclado", "musica", "cancion", "spotify", "playlist", "volumen",
         "pausa", "siguiente", "anterior", "suena", "ponme", "pon algo", "reproduc", "artista", "album", "arcoiris",
         "arco iris", "fuego", "latido", "aurora", "estrellas", "animacion", "efecto", "ram", "cinta", "rgb"]),
    "equipo": (
        ["abrir_programa", "apagar_equipo"],
        ["abre", "abrir", "abreme", "aplicacion", "programa", "apaga el computador", "apagar el computador",
         "apaga el pc", "apagar el pc", "apaga el equipo", "apagar el equipo", "apagues", "apagado", "apagalo",
         "cuando termines"]),
    "dibujo": (
        ["dibujar"],
        ["dibuj", "pinta", "pixel", "paint", "ilustr"]),
    "claude": (
        ["pensar_con_claude", "analizar_ventas_con_claude"],
        ["claude", "investiga", "a fondo", "analiza", "informe", "estudio", "estrategia", "piensa bien", "plan de"]),
    "respaldo": (
        ["copia_de_seguridad"],
        ["respaldo", "copia de seguridad", "backup", "respald"]),
    "app": (
        ["brumet_slicer"],
        ["slicer", "la app", "app de", "aplicacion de cotizar", "cotizador", "publica la", "version nueva",
         "nueva version", "arregla la app", "empleados"]),
}

PEDIDO_NUMERO = re.compile(r"\b(el|la|del|al|pedido)\s+0*\d{1,4}\b")
# Palabras cortas que son raíz de otras ("vend" → vendí, vendimos). Las demás palabras cortas deben ir sueltas:
# "pla" no puede activar el filamento con "plan" o "plata".
RAICES = {"vend", "cobr", "abon", "gast", "ped-"}
CORTO = 6  # palabras: un mensaje así sin palabras clave ("hola", "¿qué hora es?") se queda con el núcleo


def _esta(palabra: str, texto: str) -> bool:
    p = normalizar(palabra)
    if len(p) <= 4 and p not in RAICES:
        return re.search(rf"(?<!\w){re.escape(p)}(?!\w)", texto) is not None
    return p in texto


def grupos_de(texto: str, imagen: bool = False) -> list[str]:
    t = " " + normalizar(texto) + " "
    elegidos = [g for g, (_, palabras) in GRUPOS.items() if any(_esta(p, t) for p in palabras)]
    if PEDIDO_NUMERO.search(t) and "pedidos" not in elegidos:
        elegidos.append("pedidos")
    if imagen and "gastos" not in elegidos:  # una foto suele ser un recibo
        elegidos.append("gastos")
    return elegidos


def grupos_usados(herramientas: list[str]) -> list[str]:
    """Los grupos de las herramientas que ya se usaron (en el orden en que aparecen)."""
    salida: list[str] = []
    for h in herramientas:
        for g, (lista, _) in GRUPOS.items():
            if h in lista and g not in salida:
                salida.append(g)
                break
    return salida


def elegir(texto: str, activos: list[str], imagen: bool = False, anterior: str = "") -> list[str]:
    """Devuelve los grupos activos de la conversación, con los nuevos de este mensaje agregados al final.
    Un mensaje corto ("por favor", "dale", "sí, hazlo") responde a lo anterior: también cuentan las palabras del turno
    anterior (si Brumo ofreció "¿las pongo en amarillo?", hacen falta las luces).
    Si un mensaje largo no coincide con nada y no hay grupos activos, se activan todos."""
    nuevos = grupos_de(texto, imagen)
    if len(texto.split()) <= CORTO and anterior:
        nuevos += [g for g in grupos_de(anterior) if g not in nuevos]
    if not nuevos and not activos and len(texto.split()) > CORTO:
        nuevos = list(GRUPOS)
    return activos + [g for g in nuevos if g not in activos]


def herramientas(grupos: list[str]) -> list[str]:
    nombres = list(NUCLEO)
    for g in grupos:
        nombres += [h for h in GRUPOS[g][0] if h not in nombres]
    return nombres
