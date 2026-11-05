---
name: sheets-como-base-de-datos
description: Cómo usar una hoja de Google Sheets como base de datos de un negocio pequeño desde Python o desde una web estática sin servidor, sin duplicar ni dañar datos. Cubre cuenta de servicio, reintentos seguros, escribir solo las celdas necesarias, fechas como texto, una hoja falsa para pruebas y OAuth en el navegador. Úsala en cualquier app sobre Sheets. Google Sheets API v4 service account append retry idempotent.
---

# Google Sheets como base de datos (lo aprendido con Brumet)

Código de referencia: `referencia/sheets.py` (cliente Python con cuenta de servicio) y
`referencia/hoja_falsa.py` (hoja en memoria para las pruebas).

## Reglas que evitan desastres
- **Agregar filas (`:append`) NUNCA se reintenta**: si Google tarda y se repite, se duplica la venta o el gasto. Solo se reintentan las lecturas y las escrituras a celdas concretas (dan lo mismo si se repiten). Reintentar con espera (0,4 / 1,2 / 3 s) solo con 429/5xx o errores de red.
- **Cambiar un dato = escribir SOLO esa celda** (`values:batchUpdate` con el rango exacto). Nunca reescribir la fila entera: se pisan cambios que alguien hizo a la vez desde la app.
- Cada fila leída lleva su número real (`_fila`). Leer con `valueRenderOption=UNFORMATTED_VALUE`.
- Fechas y códigos como `PED-2026-0031` se guardan con `'` delante con USER_ENTERED, o se usa RAW: si no, Sheets los convierte en fecha o número.
- **No probar contra la hoja real**: usar una hoja falsa en memoria con la misma interfaz (`leer`, `actualizar`, `agregar`). Contra la real, solo lecturas.
- Todo lo que escriba va con vista previa y "sí" del usuario (ver skill `ia-local-poca-vram`).

## Acceso
- **Programa en el PC**: cuenta de servicio (`google.oauth2.service_account`, alcance `spreadsheets`), y se comparte la hoja con el correo de la cuenta como editor. Una cuenta de servicio **no tiene espacio en Drive**: para subir archivos al Drive del dueño (p. ej. fotos de recibos) hace falta OAuth con su cuenta.
- **Web estática** (sin servidor, Netlify o Cloudflare Pages): Google Identity Services en el navegador con los alcances `spreadsheets` y `drive.file`. El token se pide al usuario.
- **Aparato sin credenciales** (ESP32): hoja aparte compartida "cualquiera con el enlace → lector" y lectura por `gviz/tq?tqx=out:csv` (ver skill `encender-pc-remoto`). Nunca poner datos del negocio en una hoja pública.
- Las credenciales (`credenciales/`, `.env`) nunca van a git.

## Mantener dos programas de acuerdo
Si dos apps leen y escriben la misma hoja (la web de contabilidad y un asistente en Python), las
reglas (estados, numeración `PED-AAAA-NNNN`, categorías, columnas, fórmulas de precio) se copian de
una fuente y se anota de dónde vienen. Cuando cambian allá, hay que cambiarlas acá. Comparar las
fórmulas con casos reales (en Brumet: 303 casos de `js/costeo.js` corridos con node, diferencia 0).
Ojo con el redondeo: `Math.round` de JS sube la mitad; `round` de Python redondea la mitad al par.
