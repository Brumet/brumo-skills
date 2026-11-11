---
name: asistente-voz
description: Cómo hacer que un programa escuche y hable en español en un PC (Windows o Linux): micrófono por frases, palabra de activación con Whisper, interrumpir mientras habla, hablar rápido por frases, voces Edge/Piper/Chatterbox y cómo crear una voz ORIGINAL (no clonada) con Chatterbox. Úsala para asistentes por voz, avisos hablados, texto a voz o voz a texto locales. Voice assistant STT TTS faster-whisper wake word Piper edge-tts Chatterbox Spanish.
---

# Asistente por voz (lo aprendido con Talia, 2025–2026)

Código probado (copia de Talia, adaptar imports) en `referencia/`: `local.py` (micrófono, nombre,
interrupción, voces), `stt.py` (Whisper), `crear_voces.py` (voz original con Chatterbox).

## Escuchar
- **faster-whisper**. GPU: `medium` float16 (~0,2 s por frase, ~1,5 GB de VRAM). CPU: `small` int8. En Windows, las DLL de CUDA salen de pip (`nvidia-cublas-cu12`, `nvidia-cudnn-cu12`): agregar `site-packages/nvidia/*/bin` al PATH antes de cargar, y probar con 1 s de silencio para caer a CPU si falta algo.
- `language="es"`, `vad_filter=True`, `beam_size=1`. **NO** pasar `initial_prompt` con el nombre del asistente: Whisper lo toma como ya dicho y se salta el nombre.
- **Cortar frases sin librerías** (`Segmentador`): bloques de 30 ms a 16 kHz, nivel RMS; voz = nivel > max(mínimo, ruido × 3). El ruido de fondo se aprende solo (promedio móvil cuando no hay voz). Guardar ~10 bloques previos para no cortar la primera sílaba. Fin tras 0,6 s de silencio; descartar frases con <0,15 s de voz.
- **Palabra de activación sin entrenar**: transcribir todo y aceptar si una de las 3 primeras palabras está en una lista de cómo la escribe Whisper ("brumo", "bruno", "grumo", "promo"…). Lo de antes del nombre ("oye") se ignora. Un nombre que suene a otra palabra común (p. ej. "Talia" ~ "Dalia", "Italia") necesita su lista de variantes; si falla mucho, entrenar openWakeWord.
- Tras responder, quedar atento unos segundos sin exigir el nombre.
- **Interrumpir sin cancelar eco**: mientras habla, el micrófono también lo oye a él. Solo cuenta como interrupción el nombre o una palabra de parar ("para", "espera", "silencio", "basta"…) **que no esté en lo que él mismo está diciendo**.
- Avisos en voz alta solo si hay alguien en el PC (Windows `GetLastInputInfo`, últimos 15 min) y en horario.

## Hablar
- **Por frases**: partir el texto en frases (juntar las de <40 letras), sintetizar la siguiente mientras suena la anterior. Medido: ~0,8 s de fin de pregunta a primera palabra.
- Limpiar para voz: enlaces → "el enlace", "$45.000" → "45.000 pesos", "3 h" → "3 horas", quitar emojis y markdown, saltos de línea → punto.
- **edge-tts** (`es-MX-JorgeNeural`, `es-MX-DaliaNeural`…): la más natural gratis, pero va a Microsoft y necesita internet. Reintentar 3 veces (0 / 0,5 / 1,5 s) antes de caer a la voz local: los tropiezos de internet duran segundos y el usuario nota "que cambia de voz". Tapar antes teléfonos, correos, documentos y enlaces.
- **Piper** (local, CPU, instantáneo): `es_MX-ald-medium`. Al dueño no le gustaron `es_MX-claude-high` ni Kokoro.
- El dueño probó las voces de Edge para un personaje y **no le gustó ninguna**: suenan a locutor, no a persona.
- No proponer NaturalVoiceSAPIAdapter (voces del Narrador): usa claves sacadas de Windows; el dueño lo descartó.

## Voz expresiva y original con Chatterbox
- `chatterbox-tts` 0.1.7, `ChatterboxMultilingualTTS` (habla español, pausas y "mmm" naturales). venv aparte con torch CUDA (pesa ~5 GB). En GPU, unos segundos por frase y **~3–4 GB de VRAM**; en CPU sale cortado o tarda minutos. Agrega marca de agua Perth.
- **Voz original sin clonar a nadie** (`crear_voces.py`):
  1. Tomar 2–3 voces sintéticas base (frases de Edge) y subirles 2–3,5 semitonos con `librosa.effects.pitch_shift` para que suene más joven.
  2. `prepare_conditionals` con cada una y **promediar** `t3.speaker_emb` (renormalizado a la norma de la primera) y `gen["embedding"]`. Armar `Conditionals(T3Cond(...), gen).to(device)`. Todo en el mismo dispositivo, si no: "Expected all tensors to be on the same device".
  3. Generar una frase semilla con esa mezcla y **usar la semilla como nueva referencia**: así la voz queda una sola y estable. `conds.save("voz_X.pt")` para repetirla igual.
  4. `exaggeration` 0,65–1,0 = más actitud; `cfg_weight` 0,35–0,4 = ritmo más suelto; `temperature` 0,8.
- **Ética**: no clonar la voz de una persona real (actriz, cantante…) sin su permiso, aunque sea para uso personal. Los clips del personaje solo se usaron para medir el tono (~285 Hz, rango amplio, habla rápida) como guía; no entraron al modelo.
- Plan para usarla barata (no hecho): generar 1–2 h de audio con la voz elegida y entrenar una voz Piper con eso. Piper corre en CPU sin tarjeta. Las frases fijas se pregeneran con Chatterbox.

## Costo de tenerlo todo encendido
Whisper medium + LLM 4B + voz en GPU llenan 8 GB (ver skill `ia-local-poca-vram`). Para algo que
escucha todo el día: Whisper `small` en CPU + Piper en CPU, y la IA solo cuando se usa.
