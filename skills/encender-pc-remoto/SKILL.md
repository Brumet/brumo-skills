---
name: encender-pc-remoto
description: Cómo prender un PC apagado desde fuera de la casa sin abrir puertos ni pagar servicios. Un ESP32 conectado a un USB del PC lee cada 30 s una hoja de Google pública de solo lectura y, si hay una orden nueva, manda Wake-on-LAN. Incluye la BIOS (ErP), el botón en una web estática y la seguridad contra encendidos por cortes de luz. Úsala también para cualquier proyecto de ESP32 que reciba órdenes por una hoja de Google. Wake on LAN ESP32 remote power on Google Sheets gviz magic packet.
---

# Encender el PC desde fuera (ESP32 + hoja de Google + Wake-on-LAN)

Firmware, plantilla de configuración y guía en `referencia/`. **Aún sin compilar ni probar en el aparato** (nov 2026).

## Cómo funciona
1. Un botón en la app web estática (con OAuth de Google) lee `A1:C1` y escribe `[[contador+1, segundos_epoch, "origen"]]` con la API de Sheets v4 (`PUT ... values/A1:C1?valueInputOption=RAW`).
2. El ESP32 lee la hoja cada 30 s **sin credenciales**: la hoja se comparte como "Cualquier persona con el enlace → Lector" y se lee por gviz en CSV:
   `https://docs.google.com/spreadsheets/d/<ID>/gviz/tq?tqx=out:csv&range=A1:B1&t=<millis>` (el `t` evita la caché). Si responde 401/403, la hoja no está compartida.
3. Si el contador cambió y la orden tiene menos de 5 minutos (hora por NTP), el ESP32 manda el paquete mágico: 6×`FF` + 16×MAC = 102 bytes, por UDP a 255.255.255.255 y a la difusión de la subred, puertos 9 y 7, tres veces.

## Detalles que importan
- **Al arrancar el ESP32 solo toma el contador como referencia**: un corte de luz o un reinicio nunca prende el PC solo.
- Se reinicia solo tras 30 min sin poder leer la hoja. WiFi con reconexión automática.
- `WiFiClientSecure.setInsecure()`: lo peor que puede hacer un impostor es prender el PC, y así no se rompe cuando Google cambie de certificado. `HTTPC_STRICT_FOLLOW_REDIRECTS` (gviz redirige).
- La clave del wifi va en `config.h`, fuera de git (`firmware/**/config.h` en .gitignore). Se sube `config.example.h`.
- ESP32-C3 SuperMini: LED en el pin 8, se prende con LOW. Para que salga el monitor serie, en Arduino IDE activar "USB CDC On Boot".
- Para escribir en la hoja desde otro programa sin OAuth del usuario, usar una cuenta de servicio con permiso de editor en esa hoja.

## Del lado del PC (lo hace el dueño, no el programa)
- **BIOS** (Gigabyte A520M S2H: `Supr` → Settings → Platform Power): **ErP → Disabled** (si no, la tarjeta de red y los USB quedan sin corriente), Wake on LAN / Resume by PCI-E → Enabled, opcional AC BACK → Always On. Guardar con `F10`.
- Windows: en la tarjeta de red activar "Reactivar con Magic Packet" y apagar el "inicio rápido". Funciona por cable, no por wifi.
- Si el PC se apagó por un corte de luz, la tarjeta no escucha hasta que prenda una vez (salvo AC BACK).
- Para usarlo de lejos: TeamViewer o similar con acceso desatendido, e inicio de sesión automático si se necesita (decisión de seguridad del dueño).

## Variantes
Esto sirve para cualquier orden remota barata a un ESP32 (abrir una puerta, prender una
impresora con un relé): hoja pública de solo lectura + contador + hora + "solo órdenes frescas".
