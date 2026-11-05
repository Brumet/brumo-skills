"""Hablar con Brumo en casa: micrófono y parlante del equipo, sin Discord (Fase 6).

Modo "boton" (por defecto): el micrófono está cerrado; se abre solo cuando tocas la esfera de la cara
(o presionas espacio), escucha una frase y se cierra. Modo "nombre": escucha siempre y responde
cuando lo llamas ("Brumo, ¿cómo van las impresoras?"); queda unos segundos atento después de
responder. Todo es local: Whisper para entender y Piper para hablar.

Probar:  python -m brumo --voz
"""
from __future__ import annotations

import asyncio
import logging
import queue
import re
import sys
import threading
import time
from pathlib import Path

import numpy as np

from brumo import privacidad
from brumo.agent import es_despedida
from brumo.cara import CARA
from brumo.config import Config
from brumo.negocio.pedidos import normalizar
from brumo.voice.stt import Transcriptor

log = logging.getLogger(__name__)

TASA = 16000          # Whisper trabaja a 16 kHz
BLOQUE = 480          # 30 ms
CHAT_VOZ = 1          # conversación propia, separada de la de Discord y la consola

# Cómo suele escribir Whisper el nombre cuando lo dices rápido.
NOMBRES = {"brumo", "bruno", "brumoo", "grumo", "brumos", "promo", "bromo", "plumo", "rumo", "brumito"}


def quitar_nombre(texto: str) -> str | None:
    """'Oye Brumo, ¿qué hay hoy?' -> '¿qué hay hoy?'. None si no lo llamaron."""
    palabras = texto.strip().split()
    for i, palabra in enumerate(palabras[:3]):
        limpia = normalizar(palabra).strip(",.;:!?¡¿ ")
        if limpia in NOMBRES:
            # Lo que va antes del nombre ("oye", "hola") se ignora: Whisper a veces lo cambia ("vaya", "gracias").
            return " ".join(palabras[i + 1:]).lstrip(",.;:!¡ ").strip()
    return None


def para_anunciar(texto: str, maximo: int = 320) -> str:
    """Versión corta de un aviso para decirla en voz alta: sin borradores de WhatsApp, enlaces ni listas largas."""
    lineas = []
    for linea in texto.splitlines():
        if "wa.me" in linea or "Borradores" in linea or "borradores" in linea.lower():
            break
        if linea.strip():
            lineas.append(linea.strip())
    corto = " ".join(lineas)
    return corto if len(corto) <= maximo else corto[:maximo].rsplit(" ", 1)[0] + "…"


def inactivo_segundos() -> float:
    """Segundos desde la última vez que se tocó el teclado o el mouse. Fuera de Windows: 0 (se asume que hay alguien)."""
    if sys.platform != "win32":
        return 0.0
    import ctypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("dwTime", ctypes.c_uint)]

    info = LASTINPUTINFO()
    info.cbSize = ctypes.sizeof(info)
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
    return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000


def presente(minutos: int = 15) -> bool:
    """¿Hay alguien usando el PC? (teclado o mouse en los últimos minutos)."""
    return inactivo_segundos() < minutos * 60


PARAR = {"para", "pare", "parale", "espera", "espere", "silencio", "callate", "calla", "stop", "basta", "detente",
         "alto", "ya", "suficiente"}


def es_interrupcion(oido: str, dicho: str) -> bool:
    """¿Lo que entró por el micrófono mientras hablaba es alguien interrumpiendo?
    Sin cancelar el eco, el micrófono también oye a Brumo: solo cuenta si dicen su nombre o una palabra
    para callarlo que NO esté en lo que él mismo está diciendo."""
    palabras = [normalizar(p).strip(",.;:!?¡¿ ") for p in oido.split()]
    suyas = {normalizar(p).strip(",.;:!?¡¿ ") for p in dicho.split()}
    return any(p in (NOMBRES | PARAR) and p not in suyas for p in palabras[:4])


EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿️‍]")


def para_hablar(texto: str) -> str:
    """Quita lo que se ve bien en Discord pero suena mal en voz alta."""
    t = re.sub(r"<?https?://\S+>?", "el enlace", texto)
    t = re.sub(r"PED-\d{4}-0*(\d+)", r"pedido \1", t)
    t = re.sub(r"\$\s?([\d.]+)", r"\1 pesos", t)
    t = EMOJI.sub("", t)
    t = re.sub(r"[*_`#>|]", "", t)
    t = t.replace("·", ",").replace("—", ",").replace("«", "").replace("»", "")
    t = re.sub(r"\s*\n+\s*-?\s*", ". ", t)
    t = re.sub(r"\b(\d+)\s?h\b", lambda m: f"{m[1]} {'hora' if m[1] == '1' else 'horas'}", t)
    t = re.sub(r"\b(\d+)\s?min\b", lambda m: f"{m[1]} {'minuto' if m[1] == '1' else 'minutos'}", t)
    t = re.sub(r"([.?!])\s*\.", r"\1", t)
    t = re.sub(r"\s{2,}", " ", t).strip(" .")
    return t if t.endswith(("?", "!")) else t + "."


def partir_frases(texto: str, minimo: int = 40) -> list[str]:
    """Corta en frases para empezar a hablar antes; junta las muy cortas para que no suene entrecortado."""
    piezas = [p.strip() for p in re.split(r"(?<=[.!?¿¡;:])\s+", texto) if p.strip()]
    frases: list[str] = []
    for pieza in piezas:
        if frases and len(frases[-1]) < minimo:
            frases[-1] = f"{frases[-1]} {pieza}"
        else:
            frases.append(pieza)
    return frases


class Segmentador:
    """Junta el audio del micrófono en frases: empieza cuando sube el volumen sobre el ruido de fondo
    y termina tras un silencio. El ruido de fondo se va midiendo solo."""

    def __init__(self, sensibilidad: float = 3.0, minimo: float = 0.006, silencio: float = 0.6,
                 maximo: float = 15.0, voz_minima: float = 0.15):
        self.sensibilidad = sensibilidad
        self.minimo = minimo
        self.bloques_silencio = int(silencio * TASA / BLOQUE)
        self.bloques_max = int(maximo * TASA / BLOQUE)
        self.bloques_voz = int(voz_minima * TASA / BLOQUE)
        self.ruido = minimo
        self._previo: list[np.ndarray] = []   # un poco de audio antes de que empiece la voz
        self._frase: list[np.ndarray] = []
        self._callado = 0
        self._con_voz = 0

    def reiniciar(self) -> None:
        self._previo, self._frase, self._callado, self._con_voz = [], [], 0, 0

    def agregar(self, bloque: np.ndarray) -> np.ndarray | None:
        nivel = float(np.sqrt(np.mean(np.square(bloque)))) if len(bloque) else 0.0
        hay_voz = nivel > max(self.minimo, self.ruido * self.sensibilidad)
        if not self._frase:
            if hay_voz:
                self._frase = [*self._previo, bloque]
                self._con_voz, self._callado = 1, 0
            else:
                self.ruido = 0.95 * self.ruido + 0.05 * max(nivel, self.minimo / 3)
                self._previo = (self._previo + [bloque])[-10:]
            return None
        self._frase.append(bloque)
        if hay_voz:
            self._con_voz += 1
            self._callado = 0
        else:
            self._callado += 1
        if self._callado >= self.bloques_silencio or len(self._frase) >= self.bloques_max:
            frase, voz = np.concatenate(self._frase), self._con_voz
            self.reiniciar()
            return frase if voz >= self.bloques_voz else None
        return None




def envolvente(audio: np.ndarray, tasa: int, paso: float = 0.05) -> list[float]:
    """Volumen de la voz cada `paso` segundos (0 a 1), para que la esfera "hable" al ritmo."""
    n = max(1, int(tasa * paso))
    trozos = [audio[i:i + n] for i in range(0, len(audio), n)]
    niveles = np.array([float(np.sqrt(np.mean(np.square(t)))) if len(t) else 0.0 for t in trozos])
    tope = float(niveles.max()) if len(niveles) else 0.0
    return [round(float(x), 3) for x in (niveles / tope if tope > 0 else niveles)]


def sintetizar_edge(texto: str, voz: str) -> tuple[np.ndarray, int]:
    """Voces neuronales de Microsoft (las más naturales). Necesitan internet."""
    import io

    import edge_tts
    import soundfile as sf

    async def descargar() -> bytes:
        datos = bytearray()
        async for trozo in edge_tts.Communicate(texto, voz).stream():
            if trozo["type"] == "audio":
                datos.extend(trozo["data"])
        return bytes(datos)

    audio, tasa = sf.read(io.BytesIO(asyncio.run(descargar())), dtype="float32")
    return (audio if audio.ndim == 1 else audio.mean(axis=1)), tasa


class Voz:
    """Texto a voz: Edge (motor "edge", natural, con internet) o Piper (local). Si Edge falla, usa Piper."""

    def __init__(self, modelo: Path, parlante=None, motor: str = "piper", voz_edge: str = ""):
        from piper import PiperVoice

        self._voz = PiperVoice.load(str(modelo))
        self.parlante = parlante
        self.motor = motor
        self.voz_edge = voz_edge

    def sintetizar(self, texto: str, privado: bool = True) -> tuple[np.ndarray, int]:
        # Lo privado (datos del negocio) nunca va a Microsoft: se dice con la voz local.
        if self.motor == "edge" and self.voz_edge and not privado:
            # Las fallas suelen durar segundos (un tropiezo del internet): se reintenta antes de cambiar de voz, que
            # es lo que el dueño nota ("a veces cambia de voz").
            for espera in (0, 0.5, 1.5):
                time.sleep(espera)
                try:
                    return sintetizar_edge(texto, self.voz_edge)
                except Exception:  # noqa: BLE001 - se reintenta; sin internet sigue hablando con la voz local
                    log.info("La voz %s falló; reintento.", self.voz_edge)
            log.warning("No pude usar la voz %s (¿sin internet?); uso la voz local.", self.voz_edge)
        partes = [c.audio_float_array for c in self._voz.synthesize(texto)]
        return (np.concatenate(partes) if partes else np.zeros(0, dtype=np.float32)), self._voz.config.sample_rate

    def decir(self, texto: str, mostrar: str = "", privado: bool = True, interrumpir=None) -> bool:
        """Habla por frases: mientras suena una, ya se está preparando la siguiente (empieza mucho antes).
        `interrumpir()` se consulta mientras suena: si devuelve True, se calla al instante y devuelve False."""
        import sounddevice as sd

        frases = partir_frases(texto)
        listas: queue.Queue = queue.Queue()

        def preparar() -> None:
            for frase in frases:
                listas.put(self.sintetizar(frase, privado))
            listas.put(None)

        threading.Thread(target=preparar, daemon=True).start()
        primera = True
        while (lista := listas.get()) is not None:
            audio, tasa = lista
            if not len(audio):
                continue
            datos = {"envolvente": envolvente(audio, tasa), "paso": 0.05}
            if primera:
                datos["texto"] = mostrar or texto
                primera = False
            CARA.poner("hablando", **datos)
            sd.play(audio, tasa, device=self.parlante)
            fin = time.monotonic() + len(audio) / tasa + 0.2
            while time.monotonic() < fin:
                if interrumpir is not None and interrumpir():
                    sd.stop()
                    return False
                time.sleep(0.05)
        return True

    def pitido(self, tono: float = 880) -> None:
        import sounddevice as sd

        t = np.linspace(0, 0.12, int(0.12 * 22050), endpoint=False)
        onda = 0.25 * np.sin(2 * np.pi * tono * t) * np.hanning(len(t))
        sd.play(onda.astype(np.float32), 22050, device=self.parlante, blocking=True)


class VozLocal:
    """Dos modos (voz_local.modo): "boton" = el micrófono solo se abre al tocar la esfera de la cara;
    "nombre" = escucha siempre y responde cuando lo llaman por su nombre."""

    def __init__(self, config: Config, responder, transcriptor: Transcriptor | None = None):
        """`responder(texto) -> respuesta` se llama desde el hilo de la voz (bloquea hasta tener respuesta)."""
        self.config = config
        self.responder = responder
        self.transcriptor = transcriptor or Transcriptor(config.whisper_modelo, config.idioma, config.whisper_modelo_gpu)
        self.voz = Voz(config.voz_modelo, config.voz_parlante, config.voz_motor, config.voz_edge)
        self.segmentador = Segmentador(sensibilidad=config.voz_sensibilidad)
        self._audio: queue.Queue[np.ndarray] = queue.Queue()
        self._hablando = threading.Event()
        self._toques: queue.Queue[None] = queue.Queue()
        self.atento_hasta = 0.0
        self.interrupcion: str | None = None  # lo que dijeron al interrumpirlo ("" = solo que se callara)
        self._anuncios: queue.Queue[str] = queue.Queue()

    def anunciar(self, texto: str, maximo: int = 320) -> bool:
        """Avisos que Brumo dice solo (negocio, impresoras, recordatorios). Se dicen cuando no está ocupado,
        si hay alguien en el PC y en horario de avisos."""
        ahora = time.localtime()
        hora = ahora.tm_hour * 60 + ahora.tm_min
        desde = self.config.hora_resumen.hour * 60 + self.config.hora_resumen.minute
        hasta = self.config.avisos_hasta.hour * 60 + self.config.avisos_hasta.minute
        if self.config.voz_avisos and desde <= hora < hasta and presente():
            self._anuncios.put(para_anunciar(texto, maximo))
            return True
        return False

    def _decir_anuncios(self) -> None:
        while not self._anuncios.empty():
            texto = self._anuncios.get_nowait()
            CARA.poner("aviso")
            self.hablar(texto, privado=False)

    def tocar(self) -> None:
        """La esfera fue tocada: escuchar una frase (si ya está ocupado, se ignora)."""
        if self.config.voz_modo == "nombre":  # el micrófono ya está abierto: lo siguiente que digas vale sin el nombre
            self.atento_hasta = time.monotonic() + self.config.voz_atento_segundos
            CARA.poner("escuchando", texto="Te escucho…")
            return
        if self._toques.empty():
            self._toques.put(None)

    def _al_capturar(self, datos, _frames, _tiempo, estado) -> None:
        if estado:
            log.debug("Micrófono: %s", estado)
        # Mientras habla también se escucha (para que lo puedan interrumpir); solo se tapa durante el pitido.
        if not self._hablando.is_set():
            self._audio.put(datos[:, 0].copy())

    def _interrumpe(self, dicho: str):
        """Revisa lo que entra por el micrófono mientras Brumo habla. Devuelve una función para Voz.decir."""
        oido = Segmentador(sensibilidad=self.config.voz_sensibilidad * 1.5, maximo=4.0)

        def revisar() -> bool:
            while True:
                try:
                    bloque = self._audio.get_nowait()
                except queue.Empty:
                    return False
                frase = oido.agregar(bloque)
                if frase is None:
                    continue
                texto = self.transcriptor.transcribir_audio(frase)
                if es_interrupcion(texto, dicho):
                    log.info("✋ Me interrumpieron: %s", texto)
                    self.interrupcion = quitar_nombre(texto) or ""
                    return True
        return revisar

    def _pitar(self, tono: float = 880) -> None:
        self._hablando.set()
        try:
            self.voz.pitido(tono)
        finally:
            self._hablando.clear()

    def hablar(self, texto: str, privado: bool = True) -> None:
        dicho = para_hablar(texto)
        if self.config.voz_siempre_igual:
            # El dueño prefiere que la voz nunca cambie: siempre la natural, pero sin teléfonos, correos,
            # documentos ni enlaces (no hace falta decirlos en voz alta y así no salen del equipo).
            dicho, privado = para_hablar(privacidad.tapar(texto)), False
        self.interrupcion = None
        with self._audio.mutex:
            self._audio.queue.clear()
        completo = True
        try:
            completo = self.voz.decir(dicho, mostrar=texto, privado=privado,
                                      interrumpir=self._interrumpe(dicho) if self.config.voz_modo == "nombre" else None)
        finally:
            self.segmentador.reiniciar()
            if completo:  # lo que quedó en el micrófono era su propio eco: se bota
                time.sleep(0.2)
                with self._audio.mutex:
                    self._audio.queue.clear()
        if completo:
            CARA.poner("dormido")
        else:
            # Lo interrumpieron: lo que sigue diciendo el usuario ("…mejor dime el clima") sigue en el
            # micrófono y se atiende sin pedirle que repita el nombre.
            CARA.poner("escuchando", texto="Te escucho…")
            self.atento_hasta = time.monotonic() + self.config.voz_atento_segundos

    def responder_a(self, pedido: str) -> str:
        log.info("🎙️ Escuché: %s", pedido)
        CARA.poner("pensando", texto=f"«{pedido}»")
        try:
            respuesta, privado = self.responder(pedido)
        except Exception:  # noqa: BLE001
            log.exception("Error respondiendo por voz")
            respuesta, privado = "Tuve un problema pensando. Revisa si Ollama está encendido.", False
        log.info("🔊 Respondo (%s): %s", "voz local, tiene datos" if privado else "voz natural", respuesta)
        self.hablar(respuesta, privado)
        return respuesta

    def atender(self, frase: np.ndarray) -> None:
        """Modo nombre: solo responde si la frase lo llama (o si quedó atento tras responder)."""
        texto = self.transcriptor.transcribir_audio(frase)
        if not texto:
            return
        atento = time.monotonic() < self.atento_hasta
        pedido = quitar_nombre(texto)
        if pedido is None and not atento:
            log.debug("Ignorado (no me llamaron): %s", texto)
            return
        pedido = texto if pedido is None else pedido
        if not pedido:
            self._pitar()
            CARA.poner("escuchando")
            self.atento_hasta = time.monotonic() + self.config.voz_atento_segundos
            return
        self._pitar(660)
        respuesta = self.responder_a(pedido)
        if es_despedida(pedido):  # "es todo por ahora": deja de escuchar hasta que lo vuelvan a llamar
            self.atento_hasta = 0.0
            CARA.poner("dormido")
            return
        # Si lo interrumpieron con una nueva petición ("Brumo, mejor dime el clima"), se atiende de una.
        while self.interrupcion:
            pedido, self.interrupcion = self.interrupcion, None
            self._pitar(660)
            respuesta = self.responder_a(pedido)
        if self.interrupcion is not None:  # solo "para" / "Brumo": se calla y queda escuchando
            return
        self.atento_hasta = time.monotonic() + self.config.voz_atento_segundos
        if respuesta.rstrip().endswith("?"):  # espera tu respuesta: la esfera sigue a la vista
            CARA.poner("escuchando")

    def escuchar_una_frase(self, espera: float = 6.0) -> np.ndarray | None:
        """Modo botón: abre el micrófono, espera a que hables y lo cierra apenas terminas."""
        import sounddevice as sd

        self.segmentador.reiniciar()
        with self._audio.mutex:
            self._audio.queue.clear()
        limite = time.monotonic() + espera
        with sd.InputStream(samplerate=TASA, channels=1, dtype="float32", blocksize=BLOQUE,
                            device=self.config.voz_microfono, callback=self._al_capturar):
            while True:
                try:
                    bloque = self._audio.get(timeout=0.5)
                except queue.Empty:
                    bloque = np.zeros(BLOQUE, dtype=np.float32)
                frase = self.segmentador.agregar(bloque)
                if frase is not None:
                    return frase
                if not self.segmentador._frase and time.monotonic() > limite:
                    return None  # no dijo nada

    def turno_boton(self) -> None:
        self._pitar()
        CARA.poner("escuchando", texto="Te escucho…")
        frase = self.escuchar_una_frase()
        texto = self.transcriptor.transcribir_audio(frase) if frase is not None else ""
        if not texto:
            CARA.poner("dormido", texto="No te escuché. Tócame otra vez.")
            return
        respuesta = self.responder_a(texto)
        # Si Brumo preguntó algo (ej. confirmar una vista previa), escucha la respuesta sin volver a tocar.
        if respuesta.rstrip().endswith("?"):
            self.turno_boton()

    def correr(self) -> None:
        self.transcriptor.precargar()  # para que la primera frase no espere a que cargue Whisper
        if self.config.voz_modo == "nombre":
            self._correr_nombre()
            return
        log.info("Voz local lista: toca la esfera (o espacio) para hablarme.")
        while True:
            try:
                self._toques.get(timeout=0.5)
            except queue.Empty:
                if not self._anuncios.empty():
                    self._decir_anuncios()
                continue
            try:
                self.turno_boton()
            except Exception:  # noqa: BLE001 - la voz nunca debe morir
                log.exception("Error en un turno de voz")
                CARA.poner("dormido", texto="Tuve un problema con el micrófono.")

    def _correr_nombre(self) -> None:
        import sounddevice as sd

        log.info("Voz local lista: di '%s' para hablarme.", self.config.nombre)
        with sd.InputStream(samplerate=TASA, channels=1, dtype="float32", blocksize=BLOQUE,
                            device=self.config.voz_microfono, callback=self._al_capturar):
            while True:
                bloque = self._audio.get()
                # Se acabó el rato de atención sin que dijeras nada: la esfera vuelve a reposo (y se esconde).
                if CARA.actual.get("estado") == "escuchando" and time.monotonic() > self.atento_hasta:
                    CARA.poner("dormido", texto="")
                frase = self.segmentador.agregar(bloque)
                try:
                    if frase is not None:
                        self.atender(frase)
                    elif not self._anuncios.empty() and not self.segmentador._frase:  # nadie hablando
                        self._decir_anuncios()
                except Exception:  # noqa: BLE001 - la escucha nunca debe morir
                    log.exception("Error atendiendo una frase")


ACTIVA: VozLocal | None = None  # la voz en marcha, para que Discord y los avisos le pidan anunciar algo


def anunciar(texto: str, maximo: int = 320) -> bool:
    """Lo dice en voz alta si la voz está encendida (y se cumplen horario y presencia). False si no hay voz."""
    if ACTIVA is None:
        return False
    return ACTIVA.anunciar(texto, maximo)


def iniciar(config: Config, agente, loop: asyncio.AbstractEventLoop) -> VozLocal | None:
    """Arranca la voz en un hilo aparte; las respuestas se piden al agente en el bucle principal."""
    if not config.voz_modelo.exists():
        log.warning("Voz local apagada: no está la voz %s (ver README, 'Hablarle en casa').", config.voz_modelo.name)
        return None

    def responder(texto: str) -> tuple[str, bool]:
        respuesta = asyncio.run_coroutine_threadsafe(agente.responder(CHAT_VOZ, texto), loop).result()
        return respuesta, agente.ultima_privada.get(CHAT_VOZ, True)

    global ACTIVA
    voz = VozLocal(config, responder)
    ACTIVA = voz
    CARA.al_tocar = voz.tocar

    def correr() -> None:
        try:
            voz.correr()
        except Exception:  # noqa: BLE001
            log.exception("La voz local se detuvo (¿hay micrófono conectado?)")

    threading.Thread(target=correr, name="voz-local", daemon=True).start()
    return voz