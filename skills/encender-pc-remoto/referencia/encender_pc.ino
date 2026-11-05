// Brumo · Encender PC (ESP32, cualquier modelo con wifi: C3 SuperMini, S3, D1 mini ESP32...)
//
// Cada 30 s lee la hoja "Encender PC" (A1 = contador, B1 = hora en segundos).
// Cuando el boton "Prender PC" de la app de contabilidad sube el contador y la orden es
// de hace menos de 5 minutos, manda el "paquete magico" (Wake-on-LAN) a la tarjeta de
// red del PC. Al arrancar solo toma el contador actual como referencia: un corte de luz
// o un reinicio NUNCA prende el PC solo.
//
// Configuracion: copia config.example.h como config.h y escribe ahi el wifi de la casa.
// config.h no se sube a git (tiene la clave del wifi).

#include <WiFi.h>
#include <WiFiUdp.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <time.h>
#include "config.h"

#ifndef LED_INVERTIDO
#define LED_INVERTIDO 0
#endif

#ifndef LED_PIN
#ifdef LED_BUILTIN
#define LED_PIN LED_BUILTIN
#else
#define LED_PIN -1
#endif
#endif

const unsigned long CADA_MS = 30000;           // cada cuanto se revisa la hoja
const long VIGENCIA_S = 300;                   // una orden de hace mas de 5 min se ignora
const unsigned long SIN_LEER_REINICIAR_MS = 30UL * 60 * 1000;  // 30 min sin leer = reiniciar

WiFiUDP udp;
long ultimoContador = -1;                      // -1 = todavia no hay referencia
unsigned long ultimaLecturaOk = 0;
unsigned long proximaRevision = 0;

void led(bool prendido) {
  if (LED_PIN < 0) return;
  digitalWrite(LED_PIN, (prendido ^ LED_INVERTIDO) ? HIGH : LOW);
}

void parpadear(int veces) {
  for (int i = 0; i < veces; i++) { led(true); delay(120); led(false); delay(120); }
}

bool macDesdeTexto(const char* texto, uint8_t mac[6]) {
  unsigned int b[6];
  if (sscanf(texto, "%x%*c%x%*c%x%*c%x%*c%x%*c%x", &b[0], &b[1], &b[2], &b[3], &b[4], &b[5]) != 6) return false;
  for (int i = 0; i < 6; i++) mac[i] = (uint8_t)b[i];
  return true;
}

void enviarWoL() {
  uint8_t mac[6];
  if (!macDesdeTexto(PC_MAC, mac)) { Serial.println("PC_MAC mal escrita en config.h"); return; }
  uint8_t paquete[102];
  memset(paquete, 0xFF, 6);
  for (int i = 1; i <= 16; i++) memcpy(paquete + i * 6, mac, 6);

  IPAddress ip = WiFi.localIP(), mascara = WiFi.subnetMask();
  IPAddress difusionRed(ip[0] | ~mascara[0], ip[1] | ~mascara[1], ip[2] | ~mascara[2], ip[3] | ~mascara[3]);
  IPAddress destinos[2] = { IPAddress(255, 255, 255, 255), difusionRed };
  for (int vuelta = 0; vuelta < 3; vuelta++) {
    for (IPAddress d : destinos) {
      for (int puerto : {9, 7}) {
        udp.beginPacket(d, puerto);
        udp.write(paquete, sizeof(paquete));
        udp.endPacket();
      }
    }
    delay(200);
  }
  Serial.println("Paquete de encendido enviado.");
  parpadear(5);
}

void conectarWifi() {
  if (WiFi.status() == WL_CONNECTED) return;
  Serial.printf("Conectando al wifi %s", WIFI_NOMBRE);
  WiFi.mode(WIFI_STA);
  WiFi.setAutoReconnect(true);
  WiFi.begin(WIFI_NOMBRE, WIFI_CLAVE);
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) { delay(500); Serial.print("."); led(i % 2); }
  led(false);
  Serial.println(WiFi.status() == WL_CONNECTED ? " listo, IP " + WiFi.localIP().toString() : " no conecto (reintento luego)");
}

// Lee "contador","hora" de la hoja. true si la lectura salio bien.
bool leerHoja(long& contador, long& hora) {
  WiFiClientSecure cliente;
  // Sin verificar el certificado: lo peor que podria hacer un impostor es prender el PC,
  // y asi no hay que cambiar el programa cuando Google renueve sus certificados.
  cliente.setInsecure();
  HTTPClient http;
  http.setFollowRedirects(HTTPC_STRICT_FOLLOW_REDIRECTS);
  http.setTimeout(15000);
  String url = String("https://docs.google.com/spreadsheets/d/") + HOJA_ID +
               "/gviz/tq?tqx=out:csv&range=A1:B1&t=" + String(millis());
  if (!http.begin(cliente, url)) return false;
  int codigo = http.GET();
  String cuerpo = codigo == 200 ? http.getString() : "";
  http.end();
  if (codigo != 200) {
    Serial.printf("La hoja respondio %d%s\n", codigo,
                  codigo == 401 || codigo == 403 ? " (falta compartirla: cualquiera con el enlace, lector)" : "");
    return false;
  }
  cuerpo.replace("\"", "");
  int coma = cuerpo.indexOf(',');
  if (coma < 0) return false;
  contador = cuerpo.substring(0, coma).toInt();
  hora = cuerpo.substring(coma + 1).toInt();
  return true;
}

void revisar() {
  long contador, hora;
  if (!leerHoja(contador, hora)) return;
  ultimaLecturaOk = millis();
  if (ultimoContador < 0) {
    ultimoContador = contador;
    Serial.printf("Referencia tomada: orden #%ld\n", contador);
    return;
  }
  if (contador == ultimoContador) return;
  ultimoContador = contador;
  time_t ahora = time(nullptr);
  long edad = (long)ahora - hora;
  if (ahora < 1700000000) {
    Serial.println("Aun no tengo la hora de internet; la orden se acepta igual.");
    edad = 0;
  }
  if (edad < -VIGENCIA_S || edad > VIGENCIA_S) {
    Serial.printf("Orden #%ld vieja (%ld s), no se envia.\n", contador, edad);
    return;
  }
  Serial.printf("Orden nueva #%ld: prendiendo el PC.\n", contador);
  enviarWoL();
}

void setup() {
  Serial.begin(115200);
  if (LED_PIN >= 0) pinMode(LED_PIN, OUTPUT);
  led(false);
  delay(500);
  Serial.println("\nBrumo · Encender PC");
  conectarWifi();
  configTime(0, 0, "pool.ntp.org", "time.google.com");
  udp.begin(9);
  ultimaLecturaOk = millis();
}

void loop() {
  if (millis() >= proximaRevision) {
    proximaRevision = millis() + CADA_MS;
    conectarWifi();
    if (WiFi.status() == WL_CONNECTED) revisar();
    if (millis() - ultimaLecturaOk > SIN_LEER_REINICIAR_MS) {
      Serial.println("30 min sin poder leer la hoja: reinicio.");
      delay(200);
      ESP.restart();
    }
  }
  delay(50);
}
