---
name: automatizar-windows
description: Recetas probadas para que un programa en Python maneje el PC con Windows sin estorbar al usuario. Cubre arrancar oculto al iniciar sesión y relanzarse si se cae, oprimir botones dibujados de apps que no tienen accesibilidad (ej. Bambu Studio), abrir apps del menú Inicio, saber si hay alguien usando el PC, apagar con margen, luces RGB con OpenRGB, teclados sin soporte y poner música en Spotify gratis. Windows automation Python ctypes Win32 PrintWindow click by color startup VBS OpenRGB.
---

# Automatizar Windows desde Python (lo aprendido con Brumo)

Código de referencia en `referencia/` (copias de Brumo): `bambu_gui.py`, `equipo.py`, `luces.py`,
`animaciones.py`, `teclado.py`, `spotify.py`, `brumo-oculto.vbs`, `brumo.cmd`.

## Arrancar solo y sin ventanas
- Poner un acceso directo en `shell:startup` que apunte a un `.vbs`. El `.vbs` usa `WScript.Shell.Run "...", 0, False`: el 0 hace que no se vea ninguna ventana.
- El `.vbs` enciende `ollama.exe serve` (sin la app de Ollama en la bandeja) y un `.cmd` vigilante. El `.cmd` espera a que el servicio responda, corre el programa con salida a un log y, si se cae, lo relanza a los 30 s.
- **No** arrancar el servicio desde el `.cmd` con `start /b`: el programa queda colgado.
- Para apagarlo de verdad hay que matar el `.cmd` primero (si no, lo relanza), luego los python, y por último `ollama`/`llama-server`. Y quitar el acceso directo de Inicio si no debe volver a arrancar.

## Oprimir botones de apps "dibujadas" (sin UI Automation)
Caso Bambu Studio (wxWidgets con botones pintados a mano). Probado:
- Los botones **no aparecen** en UI Automation y **no responden** a `PostMessage` con clics. Hace falta el mouse real.
- **Ver la ventana aunque esté tapada**: `PrintWindow(hwnd, hdc, 2)` (PW_RENDERFULLCONTENT) a un bitmap → PIL → numpy. Antes, `SetProcessDpiAwareness(2)` para usar coordenadas reales.
- **Encontrar el botón por su color**: máscara de píxeles (p. ej. verde `g>150 & g-r>90 & g-b>50`), columnas con ≥6 píxeles, manchas de ≥40 px de ancho. Buscar en una caja relativa a la ventana; los números negativos se cuentan desde el borde.
- **Traer al frente desde un proceso de fondo**: Windows no lo deja con `SetForegroundWindow` a secas. Hay que hacer `AttachThreadInput` al hilo de la ventana activa, luego `ShowWindow`, `BringWindowToTop` y `SetForegroundWindow`, y soltar.
- Ponerla "siempre encima" (`SetWindowPos(-1)`) mientras se trabaja: otra ventana "siempre encima" (la de Claude) tapaba los botones. Si un estorbo choca con la zona de botones, mover o redimensionar la ventana.
- Al clic: `SetCursorPos` + `mouse_event` (bajar y subir). **Devolver el mouse y el foco** a donde estaban. Encontrar las ventanas del proceso con `EnumWindows` + `GetWindowThreadProcessId`.
- Requiere sesión desbloqueada. No sirve en un servidor sin escritorio.
- Antes de clicar algo irreversible ("Enviar"), tener un modo `probar` que llegue hasta ahí sin oprimirlo.

## Otras recetas
- **Abrir cualquier app del menú Inicio**, incluidas las de la Tienda: `Get-StartApps` (nombre + AppID), luego `explorer.exe shell:AppsFolder\<AppID>`. Tener una lista negra: registro, cmd, PowerShell, terminal, servicios, desinstaladores.
- **¿Hay alguien en el PC?** `GetLastInputInfo` contra `GetTickCount` da los segundos sin teclado ni mouse.
- **Apagar**: `shutdown /s /t 60 /c "mensaje"` (se cancela con `shutdown /a`). Siempre con confirmación.
- **Luces RGB**: OpenRGB como administrador con su servidor SDK (puerto 6742, solo local), cliente `openrgb-python`. Los efectos de fábrica cambian de nombre según el aparato: mapear nombres humanos a una lista de alternativas. Para animaciones propias (fuego, ola, aurora), usar modo Direct, calcular LED por LED en un hilo a 20 cuadros/s (~1 % de CPU), en una conexión propia. Cualquier orden nueva detiene la animación. Guardar la última orden y restaurarla al arrancar.
- **Teclado que OpenRGB no soporta** (Arzopa K81, SinoWealth 258A:002A): se descubrió solo LEYENDO el feature report 0x0A (41 bytes) mientras se cambiaba con Fn: byte 1 = efecto, byte 2 = brillo 0–5. Escribir solo valores que el aparato mismo usó.
- **Spotify sin Premium**: buscar la canción en la web (`open.spotify.com/track/<id>`) y `os.startfile("spotify:track:<id>")`. La app de escritorio la pone a sonar sola, también con cuenta gratis. Luego minimizarla.
- **Encender el PC desde fuera**: ver la skill `encender-pc-remoto`.

## Límites que puso el dueño
El programa no borra archivos, no corre comandos libres, no instala nada ni cambia ajustes de
Windows o de seguridad. BIOS, inicio automático, TeamViewer e inicio de sesión los hace el dueño:
darle los pasos.
