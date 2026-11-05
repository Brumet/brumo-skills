---
name: ia-local-poca-vram
description: Lecciones para correr un asistente o agente con IA local (Ollama, modelos de 3–8B, tarjeta de 8 GB o solo procesador) y hacerlo rápido y confiable. Úsala al diseñar o depurar un agente con herramientas (tool calling) en Ollama, decidir qué modelo y num_ctx usar, repartir la VRAM entre LLM, Whisper, visión y voz, evitar que un modelo pequeño invente acciones, o decidir si conviene IA local o Claude Code. Local LLM Ollama small model agent tools VRAM budget prompt cache.
---

# IA local con poca VRAM (lo aprendido con Brumo)

Medido en un PC con RTX 4060 de 8 GB y en un Ryzen 5 5600G sin tarjeta (2026).

## 1. Lo primero: el presupuesto de VRAM (la lección más cara)
- **Medir antes de construir**: `nvidia-smi --query-gpu=memory.used,memory.total --format=csv` y `nvidia-smi --query-compute-apps=pid,used_memory --format=csv`. En Ollama: `ollama ps`.
- Lo que ocupó cada cosa en 8 GB:
  - qwen3:4b-instruct con num_ctx 12288 → 4,2 GB.
  - Whisper medium (faster-whisper, GPU) → ~1,5 GB.
  - Windows + navegador + Bambu Studio + apps → 1,3–2 GB **siempre**.
  - Total: ~7,9 de 8 GB. Un modelo de visión (qwen2.5vl:3b) **no cabe** al lado: saca al principal (recargar 7 s, y el principal queda al 83 % en GPU, más lento).
  - Chatterbox (voz clonada/expresiva) necesita ~3–4 GB más: imposible junto con lo demás.
- **Conclusión del dueño (nov 2026)**: no vale la pena dejar una tarjeta de ~2 millones de pesos ocupada 24 h por un asistente; con la VRAM llena el PC no sirve para nada más. Brumo se apagó. Antes de proponer un asistente "siempre encendido" con IA local, sumar la VRAM de TODO y dejar ≥2 GB libres para el usuario.
- Alternativas reales:
  - **Claude Code en el PC** para pensar y construir: no ocupa la tarjeta y no tiene cobro aparte con la suscripción. Nunca usar la API de pago.
  - Un **mini PC aparte** con 32 GB de RAM, solo procesador. Es lento: ~6 tokens/s y ~2 min para leer 6.800 tokens en frío.
  - Cargar los modelos solo cuando se usan: `keep_alive` corto en vez de 24 h. Se paga con unos segundos de espera.
- Whisper en CPU (`small`/`base`, int8) y voz Piper en CPU no tocan la tarjeta.

## 2. Velocidad con Ollama
- **Caché del comienzo del mensaje**: Ollama reutiliza lo ya leído si el inicio (sistema + herramientas) no cambia. 6.600 tokens: 2,1 s la primera vez, 0,05 s después. Por eso:
  - Las instrucciones de sistema son **fijas**. Nada de fecha, memoria ni agenda dentro: eso va en un mensaje aparte DESPUÉS, o pegado al mensaje del usuario.
  - Herramientas en orden estable (núcleo primero; los grupos nuevos se agregan al final).
  - Historial: cuando se llena, **cortar a la mitad de una vez**, no un turno cada vez (cada corte obliga a releer todo).
  - `precalentar()` al arrancar: un `/api/chat` con `num_predict: 1` y las mismas instrucciones y herramientas.
- `num_ctx` de Ollama por defecto (2048–4096) es muy poco: las herramientas solas ocupan miles. Si se desborda, el modelo "olvida" las instrucciones sin avisar. Medir con `prompt_eval_count` en la respuesta.
- `keep_alive: "24h"` evita recargas, pero ocupa la VRAM todo el día (ver 1).
- No usar `format: "json"` de Ollama para salidas largas: lo vuelve lentísimo. Pedir JSON en el texto y parsear.
- Modelos que piensan: `think: false`, y quitar `<think>...</think>` de la respuesta (a veces viene sin la etiqueta de apertura).
- El dueño prefirió **qwen3:4b-instruct rápido** a qwen3:8b más listo pero lento: en voz, la velocidad pesa más.

## 3. Herramientas: mandar solo las del tema
- 35 herramientas = ~5.700 tokens por mensaje y más confusión. Solución sin IA (instantánea): un NÚCLEO fijo y GRUPOS que entran por palabras clave del mensaje (sin tildes, minúsculas; palabras ≤4 letras solo sueltas, salvo raíces como "vend"), o porque ya se usaron en la conversación. Un mensaje corto ("dale", "sí") hereda las palabras del turno anterior. Un mensaje largo sin coincidencias = todas. Lo fijo bajó de ~6.750 a ~2.330 tokens. Código: `referencia/rutas.py`.
- Una prueba que obligue a que toda herramienta nueva esté en el núcleo o en un grupo.

## 4. Modelos pequeños: cómo fallan y cómo blindarlos
- **Imitan acciones**: escriben "Listo, registré el pedido" sin llamar la herramienta. Remedio: el historial guarda **turnos completos con las llamadas a herramientas**, no solo texto. Y si la respuesta pide confirmación sin una acción preparada, se le reinyecta "[Aviso del sistema: no llamaste ninguna herramienta…]" una vez.
- **Todo lo que cambia datos o actúa afuera = vista previa + "sí"**: la herramienta no actúa; devuelve `ctx.proponer(resumen, hacer)`. El sí/no lo decide el CÓDIGO (listas `SI`/`NO` normalizadas), nunca la IA. Mientras espera, la respuesta es SOLO el resumen real + "¿Lo hago? (sí / no)" (el modelo llegó a inventar un resultado que no existía).
- **No une "por favor" con su propia oferta** ("¿quieres que las ponga en amarillo?" → "por favor" → "¿qué necesitas?"). Remedio: detectar con regex la oferta "¿quieres que …?" del turno anterior y convertir la afirmación en "Sí, hazlo: …".
- **Pasa argumentos incompletos** (con un mensaje largo de datos de cliente pasó solo nombre y producto). Remedio: extraer con regex del texto original del usuario (teléfono, cédula, correo, dirección, ciudad) dentro de la herramienta.
- Reglas cortas con ejemplos concretos ("¿quién ganó el último mundial?" → buscar_internet) funcionan mejor que principios abstractos. Decirle la fecha de hoy y que lo que sabe es viejo.
- Despedidas ("es todo", "gracias", "chao": corto y sin "?") se resuelven sin IA y borran la conversación.
- **Siempre probar con el modelo real**: las pruebas con IA falsa no muestran estas fallas.

## 5. Privacidad
- Un módulo único decide qué puede salir del PC: marcar la respuesta como privada si usó herramientas no públicas o trae teléfono, correo, documento, dinero, número de pedido o repite memoria. Lo privado no va a servicios externos (voz en la nube, búsquedas). Las búsquedas web rechazan consultas con datos personales.

Código de referencia (copia de Brumo, adaptar los imports): `referencia/agent.py`, `referencia/rutas.py`, `referencia/llm.py`, `referencia/privacidad.py`.
