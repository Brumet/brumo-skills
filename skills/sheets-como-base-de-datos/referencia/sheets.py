"""Cliente mínimo de Google Sheets con una cuenta de servicio.

Sigue las mismas reglas que js/sheets.js de brumet-contabilidad:
- Cada fila leída trae "_fila" (número real de la fila en la hoja).
- Solo se reintenta lo que se puede repetir (leer y escribir celdas concretas);
  agregar filas NUNCA se reintenta, para no duplicar ventas o gastos.
- Las fechas y el número de pedido se guardan forzados como texto (con ').
"""
from __future__ import annotations

import re
import time
from typing import Any, Protocol
from urllib.parse import quote

import httpx

BASE = "https://sheets.googleapis.com/v4/spreadsheets"
ALCANCE = ["https://www.googleapis.com/auth/spreadsheets"]
PASAJEROS = {429, 500, 502, 503, 504}
ESPERAS = [0.4, 1.2, 3.0]
CAMPOS_TEXTO_FORZADO = {"fecha_pedido", "fecha", "fecha_creacion", "periodo", "numero_pedido", "fecha_inicio"}


class Hojas(Protocol):
    def leer(self, hoja: str) -> tuple[list[str], list[dict[str, Any]]]: ...
    def actualizar(self, hoja: str, fila: int, headers: list[str], cambios: dict[str, Any]) -> None: ...
    def agregar(self, hoja: str, headers: list[str], objeto: dict[str, Any]) -> int | None: ...


def numero_a_columna(n: int) -> str:
    letras = ""
    while n > 0:
        n, resto = divmod(n - 1, 26)
        letras = chr(65 + resto) + letras
    return letras


def objeto_a_fila(headers: list[str], objeto: dict[str, Any]) -> list[Any]:
    fila = []
    for h in headers:
        valor = objeto.get(h)
        if valor is None or valor == "":
            fila.append("")
        elif h in CAMPOS_TEXTO_FORZADO:
            fila.append(f"'{valor}")
        else:
            fila.append(valor)
    return fila


class GoogleSheets:
    def __init__(self, spreadsheet_id: str, archivo_credenciales: str):
        from google.oauth2 import service_account

        self.id = spreadsheet_id
        self.credenciales = service_account.Credentials.from_service_account_file(archivo_credenciales, scopes=ALCANCE)
        self.cliente = httpx.Client(timeout=30)

    def _token(self) -> str:
        if not self.credenciales.valid:
            from google.auth.transport.requests import Request

            self.credenciales.refresh(Request())
        return self.credenciales.token

    def _pedir(self, metodo: str, url: str, repetible: bool | None = None, **kwargs) -> dict[str, Any]:
        se_puede_repetir = metodo in ("GET", "PUT") if repetible is None else repetible
        for intento in range(len(ESPERAS) + 1):
            try:
                r = self.cliente.request(metodo, url, headers={"Authorization": f"Bearer {self._token()}"}, **kwargs)
            except httpx.TransportError:
                if not se_puede_repetir or intento == len(ESPERAS):
                    raise
                time.sleep(ESPERAS[intento])
                continue
            if r.is_success:
                return r.json() if r.content else {}
            if r.status_code in PASAJEROS and se_puede_repetir and intento < len(ESPERAS):
                time.sleep(ESPERAS[intento])
                continue
            if r.status_code in PASAJEROS:
                raise RuntimeError(f"Google no respondió ({r.status_code}); suele durar poco, intenta de nuevo en un momento.")
            raise RuntimeError(f"Error de Google Sheets ({r.status_code}): {r.text[:300]}")
        raise RuntimeError("Google Sheets no respondió.")

    def leer(self, hoja: str) -> tuple[list[str], list[dict[str, Any]]]:
        rango = quote(f"{hoja}!A1:ZZ20000")
        datos = self._pedir("GET", f"{BASE}/{self.id}/values/{rango}?valueRenderOption=UNFORMATTED_VALUE")
        valores = datos.get("values") or []
        if not valores:
            return [], []
        headers = [str(h) for h in valores[0]]
        filas = []
        for indice, cruda in enumerate(valores[1:]):
            objeto: dict[str, Any] = {"_fila": indice + 2}
            for i, h in enumerate(headers):
                objeto[h] = cruda[i] if i < len(cruda) else ""
            filas.append(objeto)
        return headers, filas

    def actualizar(self, hoja: str, fila: int, headers: list[str], cambios: dict[str, Any]) -> None:
        """Escribe SOLO las celdas indicadas; el resto de la fila no se toca."""
        datos = []
        for campo, valor in cambios.items():
            columna = numero_a_columna(headers.index(campo) + 1)
            datos.append({"range": f"{hoja}!{columna}{fila}", "values": [objeto_a_fila([campo], {campo: valor})]})
        # Escribir celdas concretas da el mismo resultado si se repite: se puede reintentar.
        self._pedir("POST", f"{BASE}/{self.id}/values:batchUpdate", repetible=True,
                    json={"valueInputOption": "USER_ENTERED", "data": datos})

    def agregar(self, hoja: str, headers: list[str], objeto: dict[str, Any]) -> int | None:
        """Agrega la fila al final y devuelve su número (como agregarFila de la app), o None si Google no lo dice."""
        rango = quote(f"{hoja}!A1")
        respuesta = self._pedir(
            "POST",
            f"{BASE}/{self.id}/values/{rango}:append?valueInputOption=USER_ENTERED&insertDataOption=INSERT_ROWS",
            json={"values": [objeto_a_fila(headers, objeto)]},
        )
        rango_escrito = (respuesta.get("updates") or {}).get("updatedRange") or ""
        numero = re.search(r"![A-Z]+(\d+)", rango_escrito)
        return int(numero.group(1)) if numero else None
