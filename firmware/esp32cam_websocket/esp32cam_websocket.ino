/* =========================================================
 *  ASISTENCIA FACIAL - Firmware ESP32-CAM (AI-Thinker)
 *  Streaming de video por WebSocket + control de LEDs
 *
 *  QUE HACE:
 *   - Conecta a WiFi.
 *   - Levanta un servidor WebSocket en el puerto 81.
 *   - Envia (broadcast) cada frame JPEG capturado a todos los
 *     clientes conectados (el navegador Y el script de Python
 *     se conectan como clientes a este mismo servidor).
 *   - Escucha comandos de texto de cualquier cliente:
 *       "GREEN" -> enciende LED verde (GPIO14), apaga rojo
 *       "RED"   -> enciende LED rojo (GPIO15), apaga verde
 *       "OFF"   -> apaga ambos
 *   - Cuando cambia el estado de los LEDs, avisa a todos los
 *     clientes con un mensaje de texto "STATUS:GREEN" /
 *     "STATUS:RED" / "STATUS:OFF" para que la interfaz web
 *     se actualice.
 *
 *  LIBRERIA REQUERIDA (Arduino IDE > Herramientas > Administrar
 *  bibliotecas):
 *     "WebSockets" por Markus Sattler (Links2004/arduinoWebSockets)
 *
 *  PLACA: "AI Thinker ESP32-CAM"
 *
 *  PINES (segun montaje fisico actual):
 *   GPIO15 -> LED verde   (reconocido)
 *   GPIO14 -> LED rojo    (no reconocido)
 *   GPIO13 -> Boton fisico (reinicia el estado de los LEDs a "en espera")
 *
 *  IMPORTANTE - GPIO14 y GPIO15:
 *   Estos pines estan fisicamente compartidos con el slot de
 *   tarjeta microSD (SD_CLK y SD_CMD). Como este proyecto NO usa
 *   tarjeta SD, quedan completamente libres para usarse como
 *   salidas digitales normales, sin conflicto alguno.
 * ========================================================= */

#include "esp_camera.h"
#include <WiFi.h>
#include <WebSocketsServer.h>

// ---------------------- CONFIGURACION ----------------------

const char *WIFI_SSID     = "Hallys";
const char *WIFI_PASSWORD = "Bdpo0376*";

#define LED_VERDE_PIN 15
#define LED_ROJO_PIN  14
#define BOTON_PIN     13

// Tiempo de anti-rebote del boton (milisegundos)
const unsigned long DEBOUNCE_BOTON_MS = 250;

// Cada cuanto se envia un frame nuevo (milisegundos). 200ms = ~5 fps
const unsigned long INTERVALO_FRAME_MS = 100;

// ---------------- MAPA DE PINES ESP32-CAM (AI-THINKER) ----------------
#define PWDN_GPIO_NUM     32
#define RESET_GPIO_NUM    -1
#define XCLK_GPIO_NUM      0
#define SIOD_GPIO_NUM     26
#define SIOC_GPIO_NUM     27
#define Y9_GPIO_NUM       35
#define Y8_GPIO_NUM       34
#define Y7_GPIO_NUM       39
#define Y6_GPIO_NUM       36
#define Y5_GPIO_NUM       21
#define Y4_GPIO_NUM       19
#define Y3_GPIO_NUM       18
#define Y2_GPIO_NUM        5
#define VSYNC_GPIO_NUM    25
#define HREF_GPIO_NUM     23
#define PCLK_GPIO_NUM     22

WebSocketsServer webSocket = WebSocketsServer(81);
unsigned long ultimoFrame = 0;
unsigned long ultimoBoton = 0;

// ---------------------- CONTROL DE LEDS ----------------------

void aplicarEstado(const String &estado) {
  if (estado == "GREEN") {
    digitalWrite(LED_VERDE_PIN, HIGH);
    digitalWrite(LED_ROJO_PIN, LOW);
  } else if (estado == "RED") {
    digitalWrite(LED_VERDE_PIN, LOW);
    digitalWrite(LED_ROJO_PIN, HIGH);
  } else {
    digitalWrite(LED_VERDE_PIN, LOW);
    digitalWrite(LED_ROJO_PIN, LOW);
  }
  webSocket.broadcastTXT("STATUS:" + estado);
}

// ---------------------- EVENTOS WEBSOCKET ----------------------

void webSocketEvent(uint8_t num, WStype_t type, uint8_t *payload, size_t length) {
  switch (type) {
    case WStype_CONNECTED:
      Serial.printf("Cliente #%u conectado\n", num);
      break;

    case WStype_DISCONNECTED:
      Serial.printf("Cliente #%u desconectado\n", num);
      break;

    case WStype_TEXT: {
      String mensaje = String((char *)payload).substring(0, length);
      Serial.println("Comando recibido: " + mensaje);

      if (mensaje == "GREEN" || mensaje == "RED" || mensaje == "OFF") {
        aplicarEstado(mensaje);
      }
      break;
    }

    default:
      break;
  }
}

// ---------------------- CAMARA ----------------------

bool inicializarCamara() {
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer   = LEDC_TIMER_0;
  config.pin_d0       = Y2_GPIO_NUM;
  config.pin_d1       = Y3_GPIO_NUM;
  config.pin_d2       = Y4_GPIO_NUM;
  config.pin_d3       = Y5_GPIO_NUM;
  config.pin_d4       = Y6_GPIO_NUM;
  config.pin_d5       = Y7_GPIO_NUM;
  config.pin_d6       = Y8_GPIO_NUM;
  config.pin_d7       = Y9_GPIO_NUM;
  config.pin_xclk     = XCLK_GPIO_NUM;
  config.pin_pclk     = PCLK_GPIO_NUM;
  config.pin_vsync    = VSYNC_GPIO_NUM;
  config.pin_href     = HREF_GPIO_NUM;
  config.pin_sscb_sda = SIOD_GPIO_NUM;
  config.pin_sscb_scl = SIOC_GPIO_NUM;
  config.pin_pwdn      = PWDN_GPIO_NUM;
  config.pin_reset     = RESET_GPIO_NUM;
  config.xclk_freq_hz  = 10000000;  // Bajado de 20MHz a 10MHz: soluciona FB-OVF en varios modulos
  config.pixel_format  = PIXFORMAT_JPEG;
  config.grab_mode     = CAMERA_GRAB_LATEST;  // Descarta frames viejos en vez de acumularlos: ayuda contra FB-OVF

  if (psramFound()) {
    config.frame_size   = FRAMESIZE_VGA;   // 640x480, buen balance para reconocimiento + streaming
    config.jpeg_quality = 12;
    config.fb_count     = 2;
  } else {
    config.frame_size   = FRAMESIZE_QVGA;  // 320x240, mas liviano sin PSRAM
    config.jpeg_quality = 14;
    config.fb_count     = 1;
  }

  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Error inicializando la camara: 0x%x\n", err);
    return false;
  }

  sensor_t *s = esp_camera_sensor_get();
  if (s->id.PID == OV3660_PID) {
    s->set_vflip(s, 1);
    s->set_brightness(s, 1);
    s->set_saturation(s, -2);
  }

  return true;
}

// ---------------------- SETUP ----------------------

void setup() {
  Serial.begin(115200);
  Serial.println();

  pinMode(LED_VERDE_PIN, OUTPUT);
  pinMode(LED_ROJO_PIN, OUTPUT);
  pinMode(BOTON_PIN, INPUT);
  digitalWrite(LED_VERDE_PIN, LOW);
  digitalWrite(LED_ROJO_PIN, LOW);

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("Conectando a WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  Serial.print("Conectado. IP del ESP32-CAM: ");
  Serial.println(WiFi.localIP());
  Serial.println("Usa esta IP en server.py y en index.html");

  if (!inicializarCamara()) {
    Serial.println("No se pudo iniciar la camara. Revisa el cableado.");
  }

  webSocket.begin();
  webSocket.onEvent(webSocketEvent);
  Serial.println("Servidor WebSocket iniciado en el puerto 81");
}

// ---------------------- LOOP ----------------------

void loop() {
  webSocket.loop();

  // Boton fisico: reinicia el estado de los LEDs (apaga ambos)
  unsigned long ahoraBoton = millis();
  if (digitalRead(BOTON_PIN) == HIGH  && ahoraBoton - ultimoBoton > DEBOUNCE_BOTON_MS) {
    ultimoBoton = ahoraBoton;
    Serial.println("Boton presionado -> reiniciando estado");
    aplicarEstado("OFF");
  }

  unsigned long ahora = millis();
  if (ahora - ultimoFrame >= INTERVALO_FRAME_MS) {
    ultimoFrame = ahora;

    camera_fb_t *fb = esp_camera_fb_get();
    if (fb) {
      webSocket.broadcastBIN(fb->buf, fb->len);
      esp_camera_fb_return(fb);
    }
  }
}
