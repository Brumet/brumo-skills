# Encender el PC desde fuera de la casa

El botón **Prender PC** de la app de contabilidad sube un contador en la hoja
"Encender PC". Este ESP32, conectado al PC por un USB de atrás (que queda con
corriente con el PC apagado), lee esa hoja cada 30 s y, si hay una orden nueva de
hace menos de 5 minutos, manda el paquete de encendido (Wake-on-LAN) a la tarjeta de
red del PC (por cable; su MAC va en config.h).

Un corte de luz o un reinicio del ESP32 **nunca** prende el PC solo: al arrancar solo
toma el contador como referencia.

## Lo que hay que hacer una vez

1. **BIOS (Gigabyte A520M S2H)**: tecla `Supr` al prender → *Settings* → *Platform Power*:
   - **ErP → Disabled** (si está activado, la tarjeta de red se apaga del todo y no escucha).
   - **Wake on LAN / Resume by PCI-E Device → Enabled** (si aparece).
   - Opcional: **AC BACK → Always On** para que prenda solo cuando vuelva la luz.
   - `F10` para guardar.
2. **Compartir la hoja**: abrir "Encender PC" en Drive → Compartir →
   *Acceso general: Cualquier persona con el enlace* → **Lector**. (Solo tiene un contador
   y una hora; escribir sigue siendo solo con la cuenta del dueño.)
3. **Programar el ESP32**:
   - Instalar Arduino IDE (gratis, arduino.cc) y en *Gestor de placas* agregar **esp32 de Espressif**.
   - Copiar `config.example.h` como `config.h` y escribir el wifi de la casa.
   - Abrir `encender_pc.ino`, elegir la placa (ej. *ESP32C3 Dev Module*; en el C3 SuperMini
     activar *USB CDC On Boot: Enabled*) y el puerto, y **Subir**.
   - En el *Monitor serie* (115200) debe decir `Referencia tomada: orden #0`.
4. **Probar**: apagar el PC (dejando el ESP32 en un USB de atrás), oprimir *Prender PC* en la app
   desde el celular y esperar 1–2 minutos.

## Si no prende
- Monitor serie dice `La hoja respondio 401`: falta el paso 2.
- Dice `Paquete de encendido enviado` pero el PC no prende: revisar ErP en la BIOS, y que el PC
  se haya apagado con "Apagar" normal (no por corte de luz: tras un corte la tarjeta de red no
  escucha hasta que el PC prenda una vez, salvo con AC BACK).
- La luz del ESP32 se apaga con el PC apagado: ese puerto USB no queda con corriente; usar otro
  de atrás o un cargador de celular.
