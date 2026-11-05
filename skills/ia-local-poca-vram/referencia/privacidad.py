"""Que ningún dato del negocio salga del equipo.

Dos cosas usan servicios de afuera: la voz natural (Microsoft Edge recibe el texto que Brumo va a decir)
y la búsqueda en internet (DuckDuckGo recibe la consulta). Aquí se decide qué puede salir:
- Una respuesta es PRIVADA si usó herramientas con datos propios (pedidos, gastos, pendientes, memoria,
  recordatorios, impresoras), si contiene algo que parezca un dato (número de pedido, teléfono, correo,
  documento, dinero) o si repite algo de la memoria o la agenda. Las privadas se dicen con la voz local.
- Una búsqueda con datos así se bloquea.
"""
from __future__ import annotations

import re

from brumo.negocio.pedidos import normalizar

# Herramientas que solo tocan información pública.
PUBLICAS = {"buscar_internet", "leer_pagina", "clima", "musica", "luces", "teclado", "dibujar"}

# Siempre privados, vengan de donde vengan.
DATOS = [
    re.compile(r"PED-\d{4}-\d+", re.I),                        # número de pedido
    re.compile(r"(?:\+?57\s?)?3\d{2}[\s-]?\d{3}[\s-]?\d{4}"),   # celular colombiano
    re.compile(r"\b\d{7,}\b"),                                  # cédulas, cuentas, teléfonos
    re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+"),                     # correo
    re.compile(r"wa\.me/", re.I),                               # enlaces de WhatsApp a clientes
]
# Privados si la respuesta no salió solo de internet (ej. "el pedido 027", "debe 45 mil").
NEGOCIO = [
    re.compile(r"\b(?:pedido|el)\s+0?\d{2,4}\b", re.I),
    re.compile(r"\$\s?\d|\d[\d.]*\s?(?:pesos|mil)\b", re.I),
]


def tapar(texto: str) -> str:
    """Quita lo que nunca hace falta decir en voz alta (teléfonos, correos, documentos, enlaces de WhatsApp)
    antes de mandar el texto a la voz natural de internet."""
    for patron, cambio in ((re.compile(r"<?https?://\S+>?"), "el enlace"), (DATOS[1], "su número"),
                           (DATOS[2], "un número"), (DATOS[3], "su correo")):
        texto = patron.sub(cambio, texto)
    return texto


def tiene_datos(texto: str) -> bool:
    return any(p.search(texto or "") for p in DATOS)


def _palabras(texto: str) -> set[str]:
    return {p for p in re.findall(r"[a-z0-9]+", normalizar(texto)) if len(p) >= 6}


def respuesta_privada(texto: str, herramientas_usadas: list[str], propios: list[str] = ()) -> bool:
    """`propios`: textos de la memoria y la agenda (van en cada mensaje, así que pueden salir sin herramientas)."""
    if any(h not in PUBLICAS for h in herramientas_usadas) or tiene_datos(texto):
        return True
    if herramientas_usadas:  # solo internet o clima
        return False
    if any(p.search(texto or "") for p in NEGOCIO):
        return True
    dichas = _palabras(texto)
    return any(_palabras(p) & dichas for p in propios)
