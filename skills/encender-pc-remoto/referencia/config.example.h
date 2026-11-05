// Copia este archivo como config.h (en la misma carpeta) y completa el wifi.
// config.h NO se sube a git.

#define WIFI_NOMBRE "nombre del wifi de la casa"
#define WIFI_CLAVE  "clave del wifi"

// MAC de la tarjeta de red (por cable) del PC que se quiere prender: ipconfig /all.
#define PC_MAC "AA:BB:CC:DD:EE:FF"

// ID de la hoja (lo que va entre /d/ y /edit en su enlace). Compartida: cualquiera con el enlace, lector.
#define HOJA_ID "ID_DE_TU_HOJA"

// Luz de la placa. ESP32-C3 SuperMini: pin 8, prende con LOW (LED_INVERTIDO 1).
// Si tu placa es otra, prueba LED_INVERTIDO 0 o pon LED_PIN -1 para no usar la luz.
#define LED_PIN 8
#define LED_INVERTIDO 1
