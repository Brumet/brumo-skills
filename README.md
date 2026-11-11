# Talia skills

Lo que aprendimos construyendo **Talia** (por dentro el programa se llama `brumo`), una asistente local (voz + Discord + IA en el PC) para un
taller de impresión 3D en Colombia, guardado como *skills* para Claude Code / Agent Skills.

Cada skill es una carpeta con un `SKILL.md`: qué funciona, qué no, y por qué. Al lado va una carpeta
`referencia/` con el código que se usó de verdad. Todo se probó con aparatos reales en 2025–2026:
3 impresoras Bambu Lab A1 Combo, una RTX 4060 de 8 GB y un Ryzen 5 5600G. Lo que aún no se probó lo
dice cada skill.

*English: field notes and working code from a local assistant for a 3D-printing shop. Covers Bambu
Lab printers over LAN (MQTT, FTPS, camera, CLI slicing, printing through Bambu Studio despite the 2025
"Authorization Control" firmware), small local LLM agents on 8 GB of VRAM, Spanish voice I/O,
Windows automation, Wake-on-LAN via an ESP32 and a Google Sheet, and Google Sheets as a database.
Written in Spanish.*

## Las skills

| Skill | Para qué sirve |
|---|---|
| [bambu-lab-red-local](skills/bambu-lab-red-local/SKILL.md) | Hablar con impresoras Bambu Lab por la red local. Ver el estado (MQTT 8883), tomar fotos (puerto 6000) y bajar o subir archivos (FTPS implícito 990, con el arreglo para que no se cuelgue al subir). Encontrarlas en la red, laminar con el CLI aplanando perfiles heredados, leer gramos y tiempo de `slice_info.config`, asignar bandejas del AMS y mandar a imprimir manejando Bambu Studio. |
| [ia-local-poca-vram](skills/ia-local-poca-vram/SKILL.md) | Un agente con herramientas sobre Ollama y modelos de 3–8B: presupuesto de VRAM, aprovechar la caché del comienzo del mensaje, mandar solo las herramientas del tema, confirmar por código y no por la IA, y cómo fallan los modelos pequeños. |
| [asistente-voz](skills/asistente-voz/SKILL.md) | Escuchar por frases con faster-whisper, activarse por nombre sin entrenar nada, interrumpir sin cancelación de eco, hablar por frases (edge-tts, Piper) y crear una voz **original** con Chatterbox mezclando voces sintéticas, sin clonar a nadie. |
| [automatizar-windows](skills/automatizar-windows/SKILL.md) | Arrancar oculto y relanzarse solo, y oprimir botones dibujados que no salen en UI Automation (captura con PrintWindow + búsqueda por color + mouse real). Además: abrir apps del menú Inicio, luces RGB con OpenRGB y Spotify sin Premium. |
| [encender-pc-remoto](skills/encender-pc-remoto/SKILL.md) | Prender un PC desde el celular sin abrir puertos: un ESP32 lee una hoja de Google pública de solo lectura y manda Wake-on-LAN. |
| [sheets-como-base-de-datos](skills/sheets-como-base-de-datos/SKILL.md) | Google Sheets como base de datos sin duplicar filas ni pisar cambios. |

## Instalar

Copia las carpetas de `skills/` a `~/.claude/skills/` (en Windows: `C:\Users\<tú>\.claude\skills\`).
Claude Code las usa solas cuando el tema coincide con la descripción de cada una.

```bash
git clone https://github.com/Brumet/brumo-skills
cp -r brumo-skills/skills/* ~/.claude/skills/
```

## Sobre el código de `referencia/`

- Son copias de módulos del proyecto Talia (paquete `brumo`): importan `brumo.*`, así que hay que adaptar los
  imports al copiarlos. Sirven para leer cómo se hizo, no son una librería.
- Los nombres, costos y direcciones reales se cambiaron por valores de ejemplo.
- No se incluye ningún modelo ni programa de terceros. Si los usas, respeta sus licencias: Bambu
  Studio (AGPL-3.0), Obico (AGPL-3.0), piper-tts (GPL-3.0), Chatterbox (MIT), faster-whisper (MIT).
- No tiene relación con Bambu Lab ni con Microsoft. Manejar impresoras y equipos por tu cuenta es
  bajo tu propio riesgo: revisa la cama antes de imprimir y prueba primero sin enviar.
- No clones la voz de una persona sin su permiso.

## Licencia

[MIT](LICENSE): úsalo, cámbialo y compártelo libremente, también para proyectos comerciales.
