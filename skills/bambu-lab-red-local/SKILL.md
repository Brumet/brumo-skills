---
name: bambu-lab-red-local
description: Cómo hablar con impresoras 3D Bambu Lab (A1, A1 Combo, P1S, X1C) por la red local y con Bambu Studio. Úsala al leer el estado de la impresora (MQTT 8883), tomar fotos de la cámara (puerto 6000), bajar o subir archivos (FTPS 990), encontrar impresoras en la red, laminar o cotizar por línea de comandos (perfiles heredados, slice_info.config), asignar bandejas del AMS o mandar a imprimir (incluido el bloqueo "Authorization Control" del firmware 2025). Bambu Lab printer LAN MQTT FTPS camera slicer CLI.
---

# Impresoras Bambu Lab por la red local

Todo esto se aprendió y se probó con 3 Bambu Lab A1 Combo reales (proyecto Talia, nov 2026).
El código completo y probado (copia de Talia) está en `referencia/`, junto a este archivo:
`impresoras.py` (MQTT + FTPS), `camaras.py` (foto puerto 6000), `descubrir.py` (encontrarlas),
`bambu_gui.py` (imprimir manejando Bambu Studio), `imprimir.py` (AMS + orden directa), `cotizador.py`
(CLI, perfiles, slice_info, precio), `fallas.py` (Obico). Usa `brumo.*` en los import: adaptar al copiar.

## Acceso (lo mismo para todo)
- Usuario siempre `bblp`. La contraseña es el **código de acceso LAN** (pantalla de la impresora → Red).
- Los certificados son propios de la impresora: hay que desactivar la verificación (`CERT_NONE`, `check_hostname=False`). Es tráfico dentro de la red local.
- **Sin pedir nada al usuario**: Bambu Studio guarda serie → código en `%APPDATA%/BambuStudio/BambuStudio.conf`, sección `"access_code"`. El archivo es JSON con una línea final `# MD5 checksum ...`: se lee cortando en la última `}`. Nunca copiar los códigos a otro lado ni mostrarlos.
- **IP**: barrer la red /24 buscando el puerto 8883 abierto (asyncio, ~1,5 s de espera) y probar qué código entra: el código que funciona dice qué impresora es. Guardar IP + nombre (sin código) en un JSON y volver a buscar si deja de responder 10 min.

## Estado (MQTT, puerto 8883, TLS)
- paho-mqtt v2: `mqtt.Client(CallbackAPIVersion.VERSION2)`, `username_pw_set("bblp", codigo)`, `tls_set(cert_reqs=CERT_NONE)`, `tls_insecure_set(True)`.
- Suscribirse a `device/<serie>/report`. Al conectar, publicar en `device/<serie>/request` `{"pushing":{"sequence_id":"0","command":"pushall"}}`: llega el estado completo. **Después la A1 solo manda lo que cambió**: hay que fusionar los dicts en profundidad.
- Campos útiles dentro de `print`: `gcode_state` (IDLE, PREPARE, SLICING, RUNNING, PAUSE, FINISH, FAILED), `mc_percent`, `mc_remaining_time` (min), `layer_num`/`total_layer_num`, `subtask_name`, `gcode_file` (trae `plate_N`), `print_error`, `ams.ams[].tray[]` (`tray_type`, `tray_color` RRGGBBAA, `remain`), `vt_tray` (carrete externo).
- `print_error` 0x0300400C = **cancelada por el usuario**, no es falla. El código se muestra como `XXXX_XXXX` (`n>>16`, `n&0xFFFF`).
- Avisos: detectar la transición de estado (de trabajando a FINISH / FAILED / PAUSE), no el estado en sí.

## Cámara (puerto 6000, TLS, solo A1/P1)
- Requiere "vista en vivo LAN" activada en la impresora.
- Enviar 80 bytes: `struct.pack("<IIII", 0x40, 0x3000, 0, 0) + b"bblp".ljust(32,b"\0") + codigo.ljust(32,b"\0")`.
- Leer cabecera de 16 bytes; los 4 primeros (little endian) son el tamaño del JPEG; leer eso. Debe empezar por `FF D8`. ~2,5 s por foto.
- Detección de fallas: Obico (`obico.onnx`, AGPL, 202 MB) en CPU tarda 0,1–0,7 s, umbral 0,30. Un modelo de visión pequeño (qwen2.5vl:3b) da falsas alarmas: **avisar solo con 2 fotos seguidas con falla**. Empezar después de la capa 3. Solo mirar, nunca detener.

## Archivos (FTPS implícito, puerto 990)
- ftplib no trae FTPS implícito: subclase de `FTP_TLS` que envuelve el socket al conectar, y canal de datos que **reutiliza la sesión TLS** (`session=self.sock.session`, la impresora lo exige). Luego `login("bblp", codigo)` y `prot_p()`.
- **Subir**: reescribir `storbinary` SIN cerrar el TLS del canal de datos (`conn.unwrap()`): la impresora nunca contesta ese cierre y ftplib espera hasta el tiempo límite, aunque el archivo sí llegó.
- Los trabajos están en `/cache` y `/`. Un .3mf grande tarda ~60 s en bajar: hacerlo en segundo plano.
- Gramos por color de una impresión: bajar el .3mf y leer `Metadata/slice_info.config` de la placa (`plate_N` de `gcode_file`).

## Mandar a imprimir (lo que de verdad funciona)
- La orden directa por MQTT es `{"print":{"command":"project_file","param":"Metadata/plate_1.gcode","url":"file:///sdcard/<archivo>.gcode.3mf","use_ams":true,"ams_mapping":[...], ...}}` tras subir el archivo por FTPS. **Pero desde el firmware 2025 ("Authorization Control") la impresora la IGNORA** (se queda en IDLE) salvo en Modo desarrollador (solo LAN: se pierde Bambu Handy y la nube). No perder tiempo con eso.
- Lo que sí funciona: **manejar Bambu Studio como una persona** (ver la skill `automatizar-windows` para los clics):
  1. Elegir impresora escribiendo `app.user_last_selected_machine = <serie>` en BambuStudio.conf. La firma final es `MD5(json con \n y sin el salto final).upper()`; dejar respaldo antes.
  2. Abrir el .gcode.3mf ya laminado: `bambu-studio.exe archivo`.
  3. Aceptar avisos (botón verde de más a la derecha), oprimir "Laminar placa" → "Imprimir placa" → "Enviar". Los botones están dibujados: se buscan por su verde (#00AE42 / #3DCB73) en una captura de la ventana.
  4. Esperar ~15 s a que suba y cerrar. Ensayo real: 31 s. Necesita la sesión de Windows desbloqueada (no sirve en un servidor sin escritorio).
- **Asignar bandejas del AMS**: por cada filamento del proyecto (ranura `id` en slice_info), la bandeja del mismo tipo con el color más parecido (distancia ≤ 90); índice global = `ams_id*4 + tray_id`, 254 = externo. Si falta un color, no mandar: decir cuál falta. Un STL no trae color: sirve cualquier bandeja del mismo material.
- Antes de mandar: confirmar con el usuario y volver a revisar que la impresora esté libre.

## Laminar sin ventanas (CLI de Bambu Studio)
```
bambu-studio.exe --slice 0 --outputdir <carpeta> --export-3mf laminado.3mf
    [--load-settings "maquina.json;proceso.json"] [--load-filaments filamento.json] <modelo>
```
- `--slice 0` = todas las placas; `--slice N` = solo la placa N. Si falla deja `result.json` con `error_string` ("plate is empty", "fully inside"...).
- **El CLI no acepta perfiles heredados**: los del usuario (`%APPDATA%/BambuStudio/user/<id>/{process,machine,filament}`) solo guardan lo que cambian. Hay que **aplanarlos**: seguir `inherits` hasta los del sistema (`<instalación>/resources/profiles/BBL/...`), mezclar de padre a hijo, quitar `inherits` y poner `from: system`, `instantiation: true`, `type`.
- Un .3mf trae sus ajustes en `Metadata/project_settings.config` (`print_settings_id`, `printer_settings_id`, `filament_settings_id`): para ser fiel, laminar con ellos y cambiar solo el proceso si piden otro. Un perfil de una impresora no entra en otra (un proyecto de P1S hay que pasarlo a máquina A1).
- Resultado en `Metadata/slice_info.config` del laminado: `key="prediction"` (segundos), `key="weight"` (g; a veces 0, entonces sumar `used_g` de cada `<filament .../>`), y por filamento `id`, `type`, `color`, `used_g`.
- Trampas vistas: a veces ignora la pintura multicolor (mismo archivo 136 g en un color o 205 g en dos): repetir y quedarse con lo mayor. STL sin unidades: <1 = metros (×1000), <10 = cm (×10). Lo que no cabe en la cama: cortar en bloques que sí caben y laminar cada uno; **nunca estimar escalando copias pequeñas** (dio 20–33 % menos).
- Correr con `creationflags=CREATE_NO_WINDOW` y tiempo límite (600 s).
