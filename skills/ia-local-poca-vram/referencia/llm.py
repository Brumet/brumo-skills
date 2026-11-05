"""Cliente mínimo de Ollama (IA local) con soporte de herramientas."""
from __future__ import annotations

from typing import Any, Protocol

import base64

import httpx


class LLM(Protocol):
    async def chat(self, mensajes: list[dict[str, Any]], herramientas: list[dict[str, Any]]) -> dict[str, Any]:
        """Devuelve el mensaje del asistente: {'role': 'assistant', 'content': str, 'tool_calls': [...]}."""


class Ollama:
    def __init__(self, url: str, modelo: str, temperatura: float = 0.4, pensar: bool = False, timeout: float = 300,
                 contexto: int = 8192):
        self.url = url.rstrip("/")
        self.modelo = modelo
        self.temperatura = temperatura
        self.pensar = pensar
        # Memoria de trabajo de la IA en "tokens". Las instrucciones y herramientas ya ocupan ~3.100: con el
        # valor por defecto de Ollama (4.096) la conversación se desborda y la IA olvida sus instrucciones.
        self.contexto = contexto
        self.cliente = httpx.AsyncClient(timeout=timeout)

    async def chat(self, mensajes: list[dict[str, Any]], herramientas: list[dict[str, Any]]) -> dict[str, Any]:
        cuerpo = {
            "model": self.modelo,
            "messages": mensajes,
            "tools": herramientas,
            "stream": False,
            "think": self.pensar,
            # Mantener el modelo cargado: si Ollama lo descarga a los 5 minutos, la siguiente respuesta tarda más.
            "keep_alive": "24h",
            "options": {"temperature": self.temperatura, "num_ctx": self.contexto},
        }
        r = await self.cliente.post(f"{self.url}/api/chat", json=cuerpo)
        r.raise_for_status()
        return r.json()["message"]

    async def precalentar(self, mensajes: list[dict[str, Any]] | None = None,
                          herramientas: list[dict[str, Any]] | None = None) -> None:
        """Carga el modelo y, si se le pasan, deja leídas las instrucciones y herramientas (~6.600 tokens, ~2 s):
        así la primera pregunta no paga esa lectura. Ollama reutiliza lo leído mientras el comienzo no cambie."""
        if not mensajes:
            r = await self.cliente.post(f"{self.url}/api/generate", json={"model": self.modelo, "keep_alive": "24h",
                                                                             "options": {"num_ctx": self.contexto}})
        else:
            r = await self.cliente.post(f"{self.url}/api/chat", json={
                "model": self.modelo, "messages": mensajes, "tools": herramientas or [], "stream": False,
                "think": self.pensar, "keep_alive": "24h",
                "options": {"temperature": self.temperatura, "num_ctx": self.contexto, "num_predict": 1}})
        r.raise_for_status()

    async def ver_imagen(self, modelo: str, imagen: bytes, instruccion: str, en_segundo_plano: bool = False) -> str:
        """Le pide a un modelo con visión que describa o transcriba una imagen.
        La tarjeta de video (8 GB) no alcanza para los dos modelos: si el de visión entra, saca al principal y
        Brumo queda lento. Por eso:
        - en_segundo_plano (cámaras): corre en el procesador (~30 s, nadie espera) y el principal ni se entera.
        - si alguien espera (un recibo): usa la tarjeta y se descarga apenas termina, para que el principal vuelva."""
        cuerpo = {
            "model": modelo,
            "messages": [{"role": "user", "content": instruccion, "images": [base64.b64encode(imagen).decode()]}],
            "stream": False,
            "keep_alive": "15m" if en_segundo_plano else 0,
            "options": {"temperature": 0, **({"num_gpu": 0} if en_segundo_plano else {})},
        }
        r = await self.cliente.post(f"{self.url}/api/chat", json=cuerpo)
        r.raise_for_status()
        return r.json()["message"]["content"].strip()
