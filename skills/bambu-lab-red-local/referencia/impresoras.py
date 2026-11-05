"""Fase 5: monitorear las impresoras Bambu Lab (A1 / A1 Combo) por la red local.

Cada impresora publica su estado por MQTT en su propia IP (puerto 8883, usuario "bblp", contraseña =
código de acceso LAN). Esto funciona aunque la impresora siga conectada a la nube de Bambu: Brumo
solo LEE el estado, nunca manda órdenes de impresión.

Probar la conexión:  python -m brumo.impresoras
"""
from __future__ import annotations

import asyncio
import ftplib
import json
import logging
import os
import ssl
import threading
from collections import deque
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from time import monotonic as time_monotonic
from typing import Any, Awaitable, Callable

log = logging.getLogger(__name__)

Avisar = Callable[[str], Awaitable[None]]

ESTADOS = {
    "IDLE": "libre",
    "PREPARE": "preparando",
    "SLICING": "preparando",
    "RUNNING": "imprimiendo",
    "PAUSE": "en pausa",
    "FINISH": "terminó",
    "FAILED": "detenida (cancelada o con error)",
}
TRABAJANDO = {"PREPARE", "SLICING", "RUNNING", "PAUSE"}
# Código que pone la impresora cuando el usuario cancela la impresión (no es una falla).
CANCELADA = 0x0300400C


@dataclass
class Impresora:
    nombre: str
    ip: str
    serie: str
    codigo: str


def leer_env() -> list[Impresora]:
    """BAMBU_IMPRESORA_1=Nombre|IP|número de serie|código de acceso (y _2, _3...)."""
    lista = []
    for n in range(1, 21):
        valor = os.getenv(f"BAMBU_IMPRESORA_{n}", "").strip()
        if not valor:
            continue
        partes = [p.strip() for p in valor.split("|")]
        if len(partes) != 4 or not all(partes):
            log.warning("BAMBU_IMPRESORA_%s mal escrita: debe ser Nombre|IP|serie|código", n)
            continue
        lista.append(Impresora(*partes))
    return lista


def leer_todas() -> list[Impresora]:
    """Las de .env y, además, las que Bambu Studio conoce en este PC (sin copiar nada a mano).
    La IP de las de Bambu Studio sale de lo ya encontrado (data/impresoras.json) o se busca en la red."""
    from brumo import descubrir

    lista = leer_env()
    ya = {i.serie for i in lista}
    recuerdo = descubrir.recordar()
    for n, (serie, codigo) in enumerate(descubrir.desde_bambu_studio().items(), start=len(lista) + 1):
        if serie in ya:
            continue
        r = recuerdo.get(serie) or {}
        lista.append(Impresora(r.get("nombre") or f"Bambu {n}", r.get("ip") or "", serie, codigo))
    return lista


def fusionar(destino: dict[str, Any], cambios: dict[str, Any]) -> None:
    """La A1 manda solo lo que cambió; se va completando el estado con cada mensaje."""
    for k, v in cambios.items():
        if isinstance(v, dict) and isinstance(destino.get(k), dict):
            fusionar(destino[k], v)
        else:
            destino[k] = v


def hora(cuando: datetime) -> str:
    return f"{cuando.hour % 12 or 12}:{cuando.minute:02d} {'a. m.' if cuando.hour < 12 else 'p. m.'}"


def duracion(minutos: int) -> str:
    h, m = divmod(max(0, int(minutos)), 60)
    return f"{h} h {m} min" if h else f"{m} min"


def codigo_error(numero: Any) -> str:
    try:
        n = int(numero)
    except (TypeError, ValueError):
        return ""
    return f"{n >> 16:04X}_{n & 0xFFFF:04X}" if n else ""


def trabajo(p: dict[str, Any]) -> str:
    nombre = str(p.get("subtask_name") or p.get("gcode_file") or "").strip()
    return nombre.removesuffix(".3mf").removesuffix(".gcode")


def filamentos(p: dict[str, Any]) -> str:
    """Bandejas del AMS lite: tipo de filamento y cuánto queda (si la impresora lo sabe)."""
    bandejas = []
    for ams in (p.get("ams") or {}).get("ams") or []:
        for t in ams.get("tray") or []:
            tipo = str(t.get("tray_type") or "").strip()
            if not tipo:
                bandejas.append(f"{int(t.get('id', 0)) + 1}: vacía")
                continue
            resto = t.get("remain")
            queda = f" {resto} %" if isinstance(resto, int) and resto >= 0 else ""
            bandejas.append(f"{int(t.get('id', 0)) + 1}: {tipo}{queda}")
    return " · ".join(bandejas)


def describir(nombre: str, p: dict[str, Any] | None, ahora: datetime, conectada: bool = True) -> str:
    if not p:
        return f"🖨️ {nombre}: {'conectada, esperando datos' if conectada else 'sin conexión (¿apagada o fuera de la red?)'}"
    estado = str(p.get("gcode_state") or "").upper()
    texto = f"🖨️ {nombre}: {ESTADOS.get(estado, estado.lower() or 'estado desconocido')}"
    if not conectada:
        texto += " (sin conexión ahora; es el último dato que recibí)"
    if estado in TRABAJANDO:
        nombre_trabajo = trabajo(p)
        if nombre_trabajo:
            texto += f" «{nombre_trabajo}»"
        texto += f" — {p.get('mc_percent', 0)} %"
        capa, total = p.get("layer_num"), p.get("total_layer_num")
        if total:
            texto += f", capa {capa}/{total}"
        faltan = p.get("mc_remaining_time")
        if isinstance(faltan, (int, float)) and estado != "PAUSE":
            texto += f". Le faltan ~{duracion(faltan)}, termina a las {hora(ahora + timedelta(minutes=faltan))}"
    error = codigo_error(p.get("print_error"))
    if error and estado in {"PAUSE", "FAILED"} and int(p.get("print_error")) != CANCELADA:
        texto += f". ⚠️ Error {error} (míralo en la pantalla de la impresora)"
    fil = filamentos(p)
    if fil:
        texto += f"\n   AMS: {fil}"
    return texto


def evento(nombre: str, antes: dict[str, Any], despues: dict[str, Any], ahora: datetime) -> str | None:
    """Aviso cuando una impresión termina, se detiene o se pausa. None si no hay nada que contar."""
    de, a = str(antes.get("gcode_state") or "").upper(), str(despues.get("gcode_state") or "").upper()
    if not de or de == a:
        return None
    nombre_trabajo = trabajo(despues)
    cual = f" «{nombre_trabajo}»" if nombre_trabajo else ""
    if a == "FINISH" and de in TRABAJANDO:
        return f"✅ {nombre} terminó{cual} ({hora(ahora)}). Ya puedes retirar la pieza."
    if a == "FAILED" and de in TRABAJANDO:
        if int(despues.get("print_error") or 0) == CANCELADA:
            return f"⏹️ {nombre}: se canceló la impresión{cual}."
        error = codigo_error(despues.get("print_error"))
        return f"❌ {nombre} se detuvo{cual}" + (f" con el error {error}." if error else ".") + " Revísala."
    if a == "PAUSE" and de in TRABAJANDO:
        error = codigo_error(despues.get("print_error"))
        motivo = f" (error {error}: puede ser el filamento o un atasco)" if error else ""
        return f"⏸️ {nombre} se pausó{cual}{motivo}. Revísala."
    return None


class _FTPSImplicito(ftplib.FTP_TLS):
    """La A1 usa FTPS "implícito" (TLS desde el primer byte, puerto 990), que ftplib no trae de fábrica."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._sock = None

    @property
    def sock(self):
        return self._sock

    @sock.setter
    def sock(self, valor):
        if valor is not None and not isinstance(valor, ssl.SSLSocket):
            valor = self.context.wrap_socket(valor)
        self._sock = valor

    def ntransfercmd(self, cmd, rest=None):  # el canal de datos reutiliza la sesión TLS (lo exige la impresora)
        conn, size = ftplib.FTP.ntransfercmd(self, cmd, rest)
        if self._prot_p:
            conn = self.context.wrap_socket(conn, server_hostname=self.host, session=self.sock.session)
        return conn, size

    def storbinary(self, cmd, fp, blocksize=8192, callback=None, rest=None):
        """Como el de ftplib pero sin cerrar el TLS del canal de datos: la impresora nunca contesta ese cierre y
        ftplib se quedaba esperando hasta el tiempo límite (el archivo sí llegaba completo). Probado con una A1 real."""
        self.voidcmd("TYPE I")
        with self.transfercmd(cmd, rest) as conn:
            while trozo := fp.read(blocksize):
                conn.sendall(trozo)
                if callback:
                    callback(trozo)
        return self.voidresp()


def gramos_impresion(imp: Impresora, estado: dict[str, Any], espera: float = 15) -> list[dict] | None:
    """Los gramos por filamento (tipo y color) del trabajo que está/estuvo en la impresora, tal como los calculó
    Bambu Studio: se descarga el .3mf de la impresora (SOLO lectura, FTPS con el código LAN) y se lee su placa.
    None si no se pudo (sin red, archivo no encontrado...)."""
    import io
    import re

    from brumo.negocio import cotizador
    from brumo.negocio.pedidos import normalizar

    nombre = trabajo(estado)
    if not imp.ip or not nombre:
        return None
    m = re.search(r"plate_(\d+)", str(estado.get("gcode_file") or ""))
    placa = int(m.group(1)) if m else None
    contexto = ssl.create_default_context()
    contexto.check_hostname = False
    contexto.verify_mode = ssl.CERT_NONE  # certificado propio de la impresora; tráfico dentro de la red local
    try:
        ftp = _FTPSImplicito(context=contexto, timeout=espera)
        ftp.connect(imp.ip, 990)
        ftp.login("bblp", imp.codigo)
        ftp.prot_p()
        try:
            candidatos = []
            for carpeta in ("/cache", "/"):
                try:
                    for ruta in ftp.nlst(carpeta):
                        base = ruta.rsplit("/", 1)[-1]
                        if base.lower().endswith(".3mf") and normalizar(nombre) in normalizar(base):
                            candidatos.append(ruta if "/" in ruta else f"{carpeta.rstrip('/')}/{ruta}")
                except ftplib.all_errors:
                    continue
            if not candidatos:
                return None
            datos = io.BytesIO()
            ftp.retrbinary(f"RETR {min(candidatos, key=len)}", datos.write)
        finally:
            try:
                ftp.quit()
            except ftplib.all_errors:
                pass
        return cotizador.leer_resultado(datos, placa)["filamentos"]
    except Exception as e:  # noqa: BLE001
        log.info("No pude leer el archivo de %s en %s: %s", nombre, imp.nombre, e)
        return None


class Monitor:
    """Mantiene el último estado de cada impresora y la lista de avisos por enviar."""

    def __init__(self, impresoras: list[Impresora], tz=None):
        self.impresoras = impresoras
        self.tz = tz
        self.estados: dict[str, dict[str, Any]] = {}
        self.conectadas: set[str] = set()
        self.por_avisar: deque[str] = deque()
        self._lock = threading.Lock()
        self._clientes: dict[str, Any] = {}
        self._vistas: dict[str, float] = {}  # serie -> última vez que se supo de ella (para buscarla si cambió de IP)
        # Se llama (desde el hilo de MQTT) cuando termina una impresión: (impresora, estado) -> aviso extra o None.
        self.al_terminar: Callable[[Impresora, dict[str, Any]], str | None] | None = None

    def recibir(self, nombre: str, mensaje: dict[str, Any], ahora: datetime | None = None) -> None:
        cambios = mensaje.get("print")
        if not isinstance(cambios, dict):
            return
        ahora = ahora or datetime.now(self.tz)
        with self._lock:
            estado = self.estados.setdefault(nombre, {})
            antes = {"gcode_state": estado.get("gcode_state")}
            fusionar(estado, cambios)
            aviso = evento(nombre, antes, estado, ahora)
            if aviso:
                self.por_avisar.append(aviso)
            termino = aviso is not None and str(estado.get("gcode_state") or "").upper() == "FINISH"
            copia = json.loads(json.dumps(estado))
        imp = next((i for i in self.impresoras if i.nombre == nombre), None)
        if termino and imp is not None and self.al_terminar is not None:
            try:
                extra = self.al_terminar(imp, copia)
            except Exception:  # noqa: BLE001
                log.exception("Error al descontar el filamento")
                extra = None
            if extra:
                with self._lock:
                    self.por_avisar.append(extra)

    def resumen(self, ahora: datetime | None = None, nombre: str = "") -> str:
        ahora = ahora or datetime.now(self.tz)
        from brumo.negocio.pedidos import normalizar

        buscadas = [i for i in self.impresoras if normalizar(nombre) in normalizar(i.nombre)] if nombre else self.impresoras
        if not buscadas:
            return f"No tengo ninguna impresora llamada '{nombre}'. Tengo: " + ", ".join(i.nombre for i in self.impresoras)
        with self._lock:
            return "\n".join(describir(i.nombre, dict(self.estados.get(i.nombre) or {}), ahora, i.nombre in self.conectadas)
                             for i in buscadas)

    def tomar_avisos(self) -> list[str]:
        with self._lock:
            avisos = list(self.por_avisar)
            self.por_avisar.clear()
        return avisos

    def iniciar(self) -> None:
        """Una conexión por impresora, en hilos aparte; si se cae, paho reintenta sola.
        Las que no tienen IP (o dejan de responder) se buscan en la red cada pocos minutos."""
        for imp in self.impresoras:
            if imp.ip:
                self._conectar(imp)
        threading.Thread(target=self._buscar, name="buscar-impresoras", daemon=True).start()

    def _buscar(self) -> None:
        import time as reloj

        from brumo import descubrir

        while True:
            ahora = reloj.monotonic()
            with self._lock:
                faltan = [i for i in self.impresoras if not i.ip or
                          (i.nombre not in self.conectadas and ahora - self._vistas.get(i.serie, 0) > 600)]
            if faltan:
                try:
                    hallado = descubrir.identificar(descubrir.escanear(), {i.serie: i.codigo for i in faltan})
                except Exception:  # noqa: BLE001
                    log.exception("Error buscando las impresoras en la red")
                    hallado = {}
                for imp in faltan:
                    self._vistas[imp.serie] = ahora
                    ip = hallado.get(imp.serie)
                    if ip and ip != imp.ip:
                        log.info("Encontré %s en %s", imp.nombre, ip)
                        imp.ip = ip
                        descubrir.guardar(imp.serie, ip, imp.nombre)
                        self._conectar(imp)
            reloj.sleep(300)

    def _conectar(self, imp: Impresora) -> None:
        import paho.mqtt.client as mqtt

        viejo = self._clientes.pop(imp.serie, None)
        if viejo is not None:
            viejo.disconnect()
            viejo.loop_stop()
        cliente = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"brumo-{imp.serie}")
        cliente.username_pw_set("bblp", imp.codigo)
        # La impresora usa un certificado propio que no se puede verificar: es tráfico dentro de tu red.
        cliente.tls_set(cert_reqs=ssl.CERT_NONE)
        cliente.tls_insecure_set(True)
        cliente.reconnect_delay_set(5, 120)
        cliente.on_connect = self._al_conectar(imp)
        cliente.on_disconnect = self._al_desconectar(imp)
        cliente.on_message = self._al_mensaje(imp)
        cliente.connect_async(imp.ip, 8883, keepalive=60)
        cliente.loop_start()
        self._clientes[imp.serie] = cliente

    def publicar(self, imp: Impresora, orden: dict[str, Any]) -> None:
        """Manda una orden a la impresora. SOLO se usa para imprimir algo que el dueño confirmó con "sí"."""
        cliente = self._clientes.get(imp.serie)
        if cliente is None or imp.nombre not in self.conectadas:
            raise RuntimeError(f"no estoy conectado a {imp.nombre}")
        info = cliente.publish(f"device/{imp.serie}/request", json.dumps(orden), qos=1)
        info.wait_for_publish(10)

    def buscar(self, nombre: str) -> Impresora | None:
        from brumo.negocio.pedidos import normalizar

        n = normalizar(nombre)
        return next((i for i in self.impresoras if n and (n == normalizar(i.nombre) or n in normalizar(i.nombre))), None)

    def detener(self) -> None:
        for cliente in self._clientes.values():
            cliente.disconnect()
            cliente.loop_stop()
        self._clientes.clear()

    def _al_conectar(self, imp: Impresora):
        def al_conectar(cliente, _datos, _flags, codigo, _props):
            if codigo.is_failure:
                log.warning("%s rechazó la conexión (%s). Revisa el código de acceso.", imp.nombre, codigo)
                return
            log.info("Conectado a la impresora %s (%s)", imp.nombre, imp.ip)
            with self._lock:
                self.conectadas.add(imp.nombre)
                self._vistas[imp.serie] = time_monotonic()
            cliente.subscribe(f"device/{imp.serie}/report")
            # Pide el estado completo; después la impresora solo manda cambios.
            cliente.publish(f"device/{imp.serie}/request",
                            json.dumps({"pushing": {"sequence_id": "0", "command": "pushall"}}))
        return al_conectar

    def _al_desconectar(self, imp: Impresora):
        def al_desconectar(_cliente, _datos, _flags, codigo, _props):
            log.info("Se perdió la conexión con %s (%s)", imp.nombre, codigo)
            with self._lock:
                self.conectadas.discard(imp.nombre)
        return al_desconectar

    def _al_mensaje(self, imp: Impresora):
        def al_mensaje(_cliente, _datos, mensaje):
            try:
                self.recibir(imp.nombre, json.loads(mensaje.payload))
            except Exception:  # noqa: BLE001 - un mensaje raro no debe tumbar la conexión
                log.exception("Mensaje ilegible de %s", imp.nombre)
        return al_mensaje


async def bucle(monitor: Monitor, avisar: Avisar, tz, desde: time, hasta: time) -> None:
    """Envía los avisos de las impresoras. De noche se guardan y salen a la hora del resumen."""
    while True:
        try:
            if desde <= datetime.now(tz).time() < hasta:
                for aviso in monitor.tomar_avisos():
                    await avisar(aviso)
        except Exception:  # noqa: BLE001 - el bucle nunca debe morir
            log.exception("Error enviando avisos de impresoras")
        await asyncio.sleep(15)


if __name__ == "__main__":
    import time as reloj

    from brumo.config import cargar

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
    config = cargar()
    impresoras = leer_todas()
    if not impresoras:
        raise SystemExit("No encontré impresoras: ni en .env ni en Bambu Studio.")
    m = Monitor(impresoras, config.tz)
    m.iniciar()
    print("Buscando y escuchando las impresoras 40 segundos...")
    reloj.sleep(40)
    print(m.resumen())
    m.detener()
