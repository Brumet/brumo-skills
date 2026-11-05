"""El cerebro de Brumo: conversa con la IA local y ejecuta herramientas."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from collections import defaultdict
from datetime import datetime
from pathlib import Path
from typing import Any

from brumo import fechas, privacidad, rutas
from brumo.config import Config
from brumo.db import DB
from brumo.impresoras import Monitor
from brumo.llm import LLM
from brumo.negocio.sheets import Hojas
from brumo.tools import REGISTRO, Accion, Contexto, ejecutar

log = logging.getLogger(__name__)

MAX_PASOS = 6

REGLAS = """
Reglas:
- Usa las herramientas para crear, cambiar o borrar recordatorios, pendientes y memoria; nunca digas que hiciste algo sin haber usado la herramienta.
- Para responder qué hay pendiente o programado, usa SOLO la "Agenda actual" que viene con cada mensaje; no inventes ni respondas de conversaciones anteriores.
- Para fechas, calcula la fecha exacta a partir de la fecha y hora actual y pásala en formato ISO 'AAAA-MM-DDTHH:MM'.
- Si te falta un dato imprescindible (ej. la hora de un recordatorio), pregúntalo.
- Cuando el usuario te cuente algo que deberías recordar a futuro o te corrija, guárdalo con recordar_dato.
- Las herramientas que cambian la contabilidad (pedidos, estados, gastos) devuelven una VISTA PREVIA: muéstrasela al usuario y pregúntale si confirma. Nunca digas que ya quedó hecho; se hace cuando el usuario responda "sí".
- Los pedidos se llaman como PED-2026-0027; el usuario puede decir solo "el 027" o "el 27".
- Si el usuario manda los datos de un cliente con lo que pidió y el valor (aunque no diga "regístralo"), es un pedido NUEVO: usa crear_pedido con todos los datos. consultar_pedido es solo para pedidos que ya existen.
- Si el usuario pregunta por un cliente por su nombre ("¿cuánto debe Juan?", "el pedido de Luisa"), usa consultar_pedido con ese nombre; no le pidas el número.
- El usuario vive en Colombia: precios en pesos colombianos, noticias y datos de Colombia salvo que diga otra cosa.
- Lo que sabes llega hasta 2024 y hoy es la fecha de cada mensaje: para cualquier dato actual o que pudo cambiar (noticias, precios, dólar, deportes, resultados, quién es quién) usa buscar_internet; para el clima, la herramienta clima. Responde con lo que encuentres, corto; no inventes. Ejemplo: "¿quién ganó el último mundial?" → buscar_internet, porque pudo haber uno después de lo que sabes. SÍ tienes internet: nunca digas que no tienes acceso a datos en tiempo real; búscalo.
- Privacidad: en buscar_internet NUNCA pongas nombres de clientes, teléfonos, números de pedido, valores ni nada del negocio o del usuario; busca solo con palabras generales. Para tus propias cosas (pedidos, pendientes, impresoras) usa sus herramientas, no internet.
- Para cotizar o saber cuánto cobrar, cuánto tarda o cuánto gasta una pieza que es un archivo (.3mf/.stl), usa cotizar_modelo con el nombre del archivo; nunca inventes precios.
- Filamento: "¿cuánto filamento tengo / me queda?" → inventario_filamento. "Voy a poner un rollo nuevo …" → registrar_rollo (1000 g si no dice otra cosa; marca e impresora opcionales). Rollos ya abiertos NO se registran (no se pueden medir): dile que se registra cuando ponga uno nuevo. Nunca pidas pesar rollos.
- Dibujos: "dibuja…" → dibujar (si es una persona, persona=true con contextura, colores y expresión; "en pixel art" → estilo=pixel; "redibújala" → repetir=true). No se abre nada en el PC: por voz, la imagen se manda sola por Discord.
- Impresión: "¿cómo pongo / oriento la pieza X?" → orientar_modelo. "Imprime X en la impresora Y" → enviar_a_imprimir (pide confirmación; nunca digas que ya se mandó antes del sí).
- PC: "abre X" → abrir_programa. "Apaga el computador" → apagar_equipo (pide confirmación); "apágalo cuando termines" → cuando=al_terminar. "Apaga las luces" es luces, NO apagar_equipo.
- Negocio: "¿cómo me fue esta semana / cuánto vendí este mes?" → resumen_negocio (dias=7 o 30).
- Si llega el texto de una imagen (recibo, factura, captura de una compra), propón registrarla como gasto.
- Si en tu respuesta anterior ofreciste hacer algo y el usuario contesta "sí", "por favor", "dale" o repite el pedido, HAZLO ya con la herramienta; no vuelvas a preguntar.
- Si una herramienta falla, intenta otra vez con los datos corregidos antes de rendirte.
- Responde en español, breve y natural (tus respuestas pueden leerse en voz alta). No termines con preguntas de relleno como "¿quieres algo más?".
"""


class Agente:
    def __init__(self, config: Config, db: DB, llm: LLM, hojas: Hojas | None = None,
                 impresoras: Monitor | None = None):
        self.config = config
        self.db = db
        self.llm = llm
        self.hojas = hojas
        self.impresoras = impresoras
        self.por_confirmar: dict[int, Accion] = {}
        self.ultima_imagen: dict[int, tuple[Path, datetime]] = {}
        # Si la última respuesta tiene datos del negocio, no se puede decir con la voz de internet.
        self.ultima_privada: dict[int, bool] = {}
        self.adjuntos: dict[int, list[Path]] = {}  # fotos que dejó la última respuesta (Discord las manda)
        # Se guardan turnos completos (pregunta, herramientas usadas y respuesta): así la IA ve que
        # las acciones se hacen llamando herramientas y no las "imita" escribiendo el texto.
        self.historiales: dict[int, list[list[dict[str, Any]]]] = defaultdict(list)
        self.max_turnos = max(2, config.historial // 2)
        # Grupos de herramientas activos en cada conversación (brumo/rutas.py), en el orden en que entraron.
        self.grupos: dict[int, list[str]] = defaultdict(list)

    def _sistema(self) -> str:
        # Este texto NO cambia: así Ollama reutiliza lo ya leído (instrucciones + herramientas del núcleo). En el
        # mini PC releerlo cuesta minutos, por eso la memoria va aparte (_memoria), después de las herramientas.
        return self.config.personalidad.strip() + "\n\n" + REGLAS.strip()

    def _memoria(self) -> list[dict[str, Any]]:
        memorias = self.db.memorias(self.config.max_memorias)
        if not memorias:
            return []
        return [{"role": "system", "content": "Lo que sabes del usuario y su negocio (memoria):\n" + "\n".join(
            f"- #{m['id']} {m['texto']}" for m in reversed(memorias))}]

    @staticmethod
    def _esquemas(grupos: list[str]) -> list[dict[str, Any]]:
        return [REGISTRO[n].esquema() for n in rutas.herramientas(grupos) if n in REGISTRO]

    async def precalentar(self) -> None:
        """Deja leídas las instrucciones, las herramientas del núcleo y la memoria (se llama al arrancar y después de
        usar la IA para otra cosa, como un dibujo, que reemplaza lo que Ollama tenía leído)."""
        if hasattr(self.llm, "precalentar"):
            await self.llm.precalentar([{"role": "system", "content": self._sistema()}, *self._memoria()],
                                       self._esquemas(list(rutas.GRUPOS) if self.config.herramientas == "todas" else []))

    def _situacion(self, ctx: Contexto) -> str:
        """Fecha actual y agenda real, que acompañan al mensaje del usuario."""
        return (
            f"[Fecha y hora actual: {fechas.contexto(ctx.ahora)}]\n"
            f"[Agenda actual]\n{ejecutar('listar_pendientes', {}, ctx)}\n"
            f"Recordatorios programados:\n{ejecutar('listar_recordatorios', {}, ctx)}\n[Fin de la agenda]"
        )

    async def responder(self, chat_id: int, texto: str, ahora: datetime | None = None,
                        imagen: Path | None = None) -> str:
        ahora = ahora or datetime.now(self.config.tz)
        # La foto puede llegar en un mensaje y el "regístralo" en el siguiente: se recuerda 30 minutos.
        if imagen is not None:
            self.ultima_imagen[chat_id] = (imagen, ahora)
        elif chat_id in self.ultima_imagen:
            previa, cuando = self.ultima_imagen[chat_id]
            if (ahora - cuando).total_seconds() < 1800 and previa.exists():
                imagen = previa
        ctx = Contexto(db=self.db, chat_id=chat_id, ahora=ahora, hojas=self.hojas, impresoras=self.impresoras,
                       imagen=imagen, texto=texto)
        historial = self.historiales[chat_id]

        accion = self.por_confirmar.get(chat_id)
        if accion is None and es_despedida(texto):
            # "Es todo por ahora": se cierra la conversación sin pasar por la IA (pedido del dueño: que no siga
            # enganchado aunque le diga "todo en orden"). La próxima vez empieza de cero.
            self.olvidar_conversacion(chat_id)
            self.ultima_privada[chat_id] = False
            return DESPEDIDA
        accion = self.por_confirmar.pop(chat_id, None)
        if accion is not None:
            respuesta = await self._confirmar(accion, texto)
            if respuesta is not None:
                historial.append([{"role": "user", "content": texto}, {"role": "assistant", "content": respuesta}])
                self._recortar(chat_id)
                self.ultima_privada[chat_id] = True  # confirmar algo siempre toca datos del negocio
                return respuesta

        situacion = self._situacion(ctx)
        # "por favor" / "sí" después de que Brumo ofreció algo: el modelo pequeño no lo une con la oferta (pasó con
        # "¿quieres que las ponga en amarillo?" → "por favor" → "¿qué necesitas?"). Se le dice explícito.
        oferta = oferta_pendiente(historial[-1] if historial else [])
        pedido = f"Sí, hazlo: {oferta}." if oferta and es_afirmacion(texto) else texto
        turno: list[dict[str, Any]] = [{"role": "user", "content": f"{situacion}\n\n{pedido}"}]
        usadas: list[str] = []
        # Solo las herramientas que vienen al caso (brumo/rutas.py): menos que leer y menos con qué confundirse.
        if self.config.herramientas == "todas":
            self.grupos[chat_id] = list(rutas.GRUPOS)
        else:
            anterior = " ".join(str(m.get("content") or "") for m in (historial[-1] if historial else [])
                                if m.get("role") in ("user", "assistant"))
            self.grupos[chat_id] = rutas.elegir(texto, self.grupos[chat_id], imagen is not None, anterior)
        esquemas = self._esquemas(self.grupos[chat_id])

        guardar: list[dict[str, Any]] = [{"role": "user", "content": texto}]
        reintento_hecho = False
        for _ in range(MAX_PASOS):
            previos = [m for t in historial for m in t]
            mensajes = [{"role": "system", "content": self._sistema()}, *self._memoria(), *previos, *turno]
            respuesta = await self.llm.chat(mensajes, esquemas)
            llamadas = respuesta.get("tool_calls") or []
            turno.append({"role": "assistant", "content": respuesta.get("content", ""), "tool_calls": llamadas} if llamadas
                         else {"role": "assistant", "content": respuesta.get("content", "")})
            if not llamadas:
                # Un modelo pequeño a veces "imita" una vista previa sin llamar la herramienta.
                # Si pide confirmación sin una acción real preparada, se le exige usar la herramienta.
                if ctx.accion_propuesta is None and pide_confirmacion(respuesta.get("content", "")):
                    if not reintento_hecho:
                        reintento_hecho = True
                        turno.append({"role": "user", "content": "[Aviso del sistema: no llamaste ninguna herramienta, "
                                      "así que no hay nada preparado para confirmar. Llama ahora la herramienta que corresponde.]"})
                        continue
                    turno.append({"role": "assistant", "content": "Perdón, no logré preparar eso bien y no guardé nada. "
                                  "¿Me lo repites con los datos?"})
                break
            guardar.append({"role": "assistant", "content": "", "tool_calls": llamadas})
            for llamada in llamadas:
                funcion = llamada.get("function", {})
                nombre = funcion.get("name", "")
                usadas.append(nombre)
                argumentos = funcion.get("arguments") or {}
                if isinstance(argumentos, str):
                    try:
                        argumentos = json.loads(argumentos)
                    except json.JSONDecodeError:
                        argumentos = {}
                # Las herramientas pueden tardar (Google Sheets): se corren aparte para no congelar a Brumo.
                resultado = await asyncio.to_thread(ejecutar, nombre, argumentos, ctx)
                log.info("herramienta %s(%s) -> %s", nombre, argumentos, resultado)
                turno.append({"role": "tool", "tool_name": nombre, "content": resultado})
                guardar.append({"role": "tool", "tool_name": nombre, "content": resultado[:400]})
        else:
            turno.append({"role": "assistant", "content": "Uy, me enredé con eso. ¿Me lo dices de otra forma?"})

        final = limpiar(turno[-1].get("content", "")) or "Listo."
        self.adjuntos[chat_id] = list(ctx.adjuntos)
        if ctx.accion_propuesta is not None:
            self.por_confirmar[chat_id] = ctx.accion_propuesta
            # Mientras algo espera el "sí", se muestra SOLO la vista previa real: el modelo pequeño a veces
            # escribe un resultado inventado (ej. un análisis de Claude que todavía no se ha hecho).
            final = f"{ctx.accion_propuesta.resumen}\n\n¿Lo hago? (sí / no)"
        # Al historial no pasa la agenda ni los avisos internos, para no gastar memoria del modelo.
        historial.append([*guardar, {"role": "assistant", "content": final}])
        self._recortar(chat_id)
        propios = [r["texto"] for r in (*self.db.memorias(self.config.max_memorias), *self.db.pendientes_abiertos(),
                                        *self.db.recordatorios_activos())]
        self.ultima_privada[chat_id] = privacidad.respuesta_privada(final, usadas, propios)
        return final

    async def _confirmar(self, accion: Accion, texto: str) -> str | None:
        """Responde a una acción en espera sin pasar por la IA, para que nunca se haga sin un "sí" claro.
        Devuelve None si el mensaje no es ni sí ni no (la acción se descarta y se sigue normal)."""
        if es_si(texto):
            try:
                return await asyncio.to_thread(accion.hacer)
            except Exception as e:  # noqa: BLE001
                log.exception("Error ejecutando acción confirmada")
                return f"❌ No pude hacerlo: {e}"
        if es_no(texto):
            return "Listo, no hice nada. 👍"
        return None

    def _recortar(self, chat_id: int) -> None:
        """Cuando el historial se llena se corta a la mitad de una vez (no un turno cada vez): cada corte obliga a
        releer la conversación, y así pasa cada varios mensajes y no en todos. Los grupos de herramientas vuelven a
        ser los de lo que quedó."""
        historial = self.historiales[chat_id]
        if len(historial) <= self.max_turnos:
            return
        del historial[:len(historial) - self.max_turnos // 2]
        usadas = [m.get("tool_name", "") for t in historial for m in t if m.get("role") == "tool"]
        self.grupos[chat_id] = rutas.grupos_usados(usadas)

    def olvidar_conversacion(self, chat_id: int) -> None:
        self.historiales.pop(chat_id, None)
        self.grupos.pop(chat_id, None)
        self.por_confirmar.pop(chat_id, None)


def pide_confirmacion(texto: str) -> bool:
    t = _limpio(texto)
    return any(x in t for x in ("vista previa", "confirmas", "confirma ", "si o no", "si / no", "si/no", "responde si"))


SI = {"si", "s", "sip", "dale", "ok", "okay", "listo", "confirmo", "confirmado", "confirmar", "hagale", "de una",
      "claro", "correcto", "adelante", "va", "hazlo", "si hazlo", "si dale", "si senor", "obvio", "afirmativo"}
NO = {"no", "nop", "cancela", "cancelar", "cancelalo", "mejor no", "no gracias", "dejalo", "olvidalo", "negativo"}


def _limpio(texto: str) -> str:
    from brumo.negocio.pedidos import normalizar

    return " ".join(normalizar(texto).replace(",", " ").replace(".", " ").replace("!", " ").split())


OFERTA = re.compile(r"¿\s*(?:quieres|deseas|te gustar[ií]a|prefieres)\s+que\s+([^?¿]{3,120})\?", re.IGNORECASE)
AFIRMACION = {"por favor", "porfa", "porfavor", "si por favor", "por fa", "hagale pues", "si porfa", "eso", "eso si"}


def oferta_pendiente(turno_anterior: list[dict[str, Any]]) -> str | None:
    """Lo que Brumo ofreció al final de su última respuesta ("¿Quieres que las ponga en amarillo?")."""
    final = next((m.get("content") or "" for m in reversed(turno_anterior) if m.get("role") == "assistant"), "")
    hallados = OFERTA.findall(final)
    return hallados[-1].strip() if hallados else None


def es_afirmacion(texto: str) -> bool:
    return es_si(texto) or _limpio(texto) in AFIRMACION


DESPEDIDA = "Listo, aquí estaré. 👋"
DESPEDIDAS = ("es todo", "seria todo", "era todo", "nada mas", "todo en orden", "todo esta en orden", "todo bien",
              "hasta luego", "hasta manana", "chao", "adios", "ya no mas", "puedes descansar", "descansa",
              "no necesito nada", "eso seria")
GRACIAS = {"gracias", "muchas gracias", "listo gracias", "ok gracias", "vale gracias", "gracias brumo", "listo"}


def es_despedida(texto: str) -> bool:
    """'Es todo por ahora', 'eso es todo', 'todo en orden, gracias', 'chao'… (frases cortas, sin pregunta)."""
    if "?" in texto or "¿" in texto:
        return False
    t = _limpio(texto)
    if len(t.split()) > 8:
        return False
    return t in GRACIAS or any(d in t for d in DESPEDIDAS)


def es_si(texto: str) -> bool:
    t = _limpio(texto)
    return t in SI or (t.startswith("si ") and len(t.split()) <= 4)


def es_no(texto: str) -> bool:
    t = _limpio(texto)
    return t in NO or (t.startswith("no ") and len(t.split()) <= 4)


def limpiar(texto: str) -> str:
    """Quita el razonamiento <think>...</think> que algunos modelos dejan en la respuesta
    (a veces sin la etiqueta de apertura)."""
    if "</think>" in texto:
        texto = texto.rsplit("</think>", 1)[1]
    return texto.replace("<think>", "").strip()
