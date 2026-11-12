/* =====================================================================
 *  ASISTENCIA FACIAL - Firmware ESP32-CAM (AI-Thinker) - version autonoma
 *
 *  QUE HACE (sin PC):
 *   1. Al encender, se conecta solo al Wi-Fi guardado. Si no hay Wi-Fi
 *      configurado o no logra conectarse, abre un PORTAL CAUTIVO: una red
 *      "ASISTENCIA-XXXX" a la que se conecta un celular para escribir el
 *      Wi-Fi, el servidor (VPS), el identificador y el token de la camara.
 *   2. Abre UNA conexion WebSocket segura (WSS, puerto 443) HACIA la VPS en
 *      /ws/dispositivo, autenticada con su identificador y token. Al ser una
 *      conexion saliente, funciona detras del NAT/router del aula sin abrir
 *      puertos.
 *   3. Solo transmite fotogramas JPEG cuando el servidor lo pide ("MODE
 *      ACTIVE"): en las franjas de clase de su aula, desde 15 min antes.
 *   4. Enciende el LED verde o rojo segun lo que le ordene el servidor.
 *   5. Se reconecta solo si se cae el Wi-Fi o el servidor, y se reinicia si
 *      la caida se prolonga. Se actualiza por Wi-Fi (OTA) cuando el servidor
 *      publica una version nueva.
 *
 *  POR QUE WEBSOCKET SEGURO (y no HTTP POST ni MQTT):
 *   - El cifrado TLS cuesta 1-2 s y ~40 KB de RAM en la ESP32. Con WebSocket
 *     se negocia UNA vez y la conexion queda abierta; con HTTP POST se
 *     pagaria en cada foto.
 *   - Es bidireccional: el servidor responde GREEN/RED por el mismo canal
 *     en milisegundos, sin que la camara tenga que preguntar.
 *   - MQTT obligaria a instalar y mantener un broker extra sin ventaja aqui.
 *
 *  LEDS (patrones):
 *   Verde fijo ............ ingreso permitido (lo ordena el servidor)
 *   Rojo fijo ............. ingreso denegado / rostro no reconocido
 *   Apagados .............. conectado y listo (o en reposo fuera de clase)
 *   Rojo parpadeo lento ... sin Wi-Fi
 *   Ambos parpadeando ..... con Wi-Fi pero sin conexion con la VPS
 *   Verde/rojo alternando . portal de configuracion abierto
 *   Verde parpadeo rapido . actualizando firmware (OTA)
 *   Rojo parpadeo rapido .. error de camara (se reinicia sola)
 *
 *  BOTON (GPIO13, activo en ALTO, como el firmware original):
 *   Un click ............... apaga los LED (vuelve a "en espera")
 *   No reinicia ni abre el portal. El portal se abre solo cuando la placa no
 *   tiene Wi-Fi configurado o no encuentra la red guardada.
 *
 *  PINES - USO SEGURO EN LA ESP32-CAM AI-THINKER:
 *   Ocupados por la camara OV2640 (NO usar):
 *     0 (XCLK), 5, 18, 19, 21, 22, 23, 25, 26, 27, 32 (PWDN), 34, 35, 36, 39
 *   Ocupados por la PSRAM (NO usar): 16 (CS), 17 (CLK)
 *   Ocupados por la memoria flash interna (NO usar): 6 a 11
 *   UART0 de programacion/monitor serie: 1 (TX), 3 (RX)
 *   GPIO4  -> LED flash blanco de alta potencia: se fuerza APAGADO
 *   GPIO12 -> pin de arranque (voltaje de la flash): NO conectar nada que
 *             lo ponga en ALTO al encender, o la placa no arranca
 *   GPIO2  -> pin de arranque: debe quedar libre/bajo para programar
 *   GPIO14, GPIO15, GPIO13 -> compartidos con la ranura microSD. Este
 *             proyecto NO usa microSD, por eso quedan libres:
 *       GPIO15 LED verde: es pin de arranque (MTDO). Un LED con
 *              resistencia a GND solo silencia el log de arranque: inofensivo.
 *       GPIO14 LED rojo: emite un pulso al arrancar (el LED puede
 *              titilar un instante al encender): inofensivo.
 *       GPIO13 boton: sin funcion de arranque.
 *     NO insertar una tarjeta microSD: entraria en conflicto con estos pines.
 *   GPIO33 -> LED rojo pequeno de la placa (activo en bajo), libre si se
 *             necesitara otro indicador.
 *
 *  ALIMENTACION: fuente estable de 5 V / 2 A. Con menos, el Wi-Fi + camara
 *  provocan reinicios por "brownout".
 *
 *  COMPILAR: lo hace desplegar.ps1 (arduino-cli) con
 *   placa  esp32:esp32:esp32cam (core 2.0.14)
 *   particion "Minimal SPIFFS (1.9MB APP with OTA)" -> obligatoria para OTA
 *   librerias: WebSockets (Markus Sattler) 2.7.x, WiFiManager (tzapu) 2.0.17
 * ===================================================================== */

#include "esp_camera.h"
#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <Update.h>
#include <Preferences.h>
#include <Ticker.h>
#include <esp_task_wdt.h>
#include <esp_wifi.h>
#include <WiFiManager.h>
#include <WebSocketsClient.h>

#include "version.h"          // FW_VERSION (lo escribe desplegar.ps1)
#include "config_defaults.h"  // valores iniciales (lo escribe desplegar.ps1)
#include "certificados.h"     // CA raiz de la VPS (lo escribe desplegar.ps1)

#ifndef XCLK_CAMARA_HZ
#define XCLK_CAMARA_HZ 20000000   // reloj de la camara (ver inicializarCamara)
#endif

// ---------------------- PINES ----------------------
#define LED_VERDE_PIN 15
#define LED_ROJO_PIN  14
#define BOTON_PIN     13
#define FLASH_LED_PIN  4

// Camara AI-Thinker
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

// ---------------------- TIEMPOS ----------------------
const unsigned long DEBOUNCE_BOTON_MS        = 80;
const unsigned long WS_REINTENTO_MS          = 5000;
const unsigned long WIFI_REINTENTO_MS        = 30000;
const unsigned long REINICIO_SIN_WIFI_MS     = 3UL * 60 * 1000;
const unsigned long REINICIO_SIN_SERVIDOR_MS = 10UL * 60 * 1000;
const unsigned long ESTADISTICAS_MS          = 60000;
const unsigned long LED_RESULTADO_MAX_MS     = 8000;   // seguridad si el servidor no manda OFF
const int64_t       FOTO_MAX_ANTIGUEDAD_MS   = 400;    // fotos mas viejas se descartan antes de enviar
const uint32_t      WDT_SEGUNDOS             = 30;
const uint16_t      PORTAL_TIMEOUT_S         = 180;
const char         *RUTA_WS                  = "/ws/dispositivo";

// ---------------------- ESTADO ----------------------
struct Config {
  String servidor;
  uint16_t puerto;
  uint8_t seguridad;   // 2 = WSS verificando certificado, 1 = WSS sin verificar, 0 = WS sin cifrar
  String id;
  String token;
} cfg;

enum Patron : uint8_t { P_APAGADO, P_VERDE, P_ROJO, P_SIN_WIFI, P_SIN_SERVIDOR, P_PORTAL, P_OTA, P_ERROR };

Preferences prefs;
WebSocketsClient ws;
Ticker tickerLed;
volatile Patron patron = P_SIN_WIFI;
volatile uint32_t tickLed = 0;

bool camaraOk = false;
bool wsConectado = false;
bool modoActivo = false;           // el servidor pidio transmitir
bool otaEnCurso = false;
String resultado = "OFF";          // GREEN / RED / OFF ordenado por el servidor
String otaPendiente = "";
String mensajePendiente = "";
String headersWs;
unsigned long resultadoDesde = 0;
unsigned long intervaloMs = 100;   // lo ajusta el servidor con "CFG"
unsigned long ultimoFrame = 0;
unsigned long ultimasEstadisticas = 0;
unsigned long sinWifiDesde = 0;
unsigned long sinServidorDesde = 0;
unsigned long ultimoReintentoWifi = 0;
uint32_t framesEnviados = 0;

// ---------------------- LEDS ----------------------

void animarLeds() {  // cada 100 ms (Ticker)
  tickLed++;
  bool v = false, r = false;
  switch (patron) {
    case P_VERDE:        v = true; break;
    case P_ROJO:         r = true; break;
    case P_SIN_WIFI:     r = (tickLed % 10) < 5; break;
    case P_SIN_SERVIDOR: v = r = (tickLed % 10) < 5; break;
    case P_PORTAL:       v = (tickLed % 10) < 5; r = !v; break;
    case P_OTA:          v = tickLed % 2; break;
    case P_ERROR:        r = tickLed % 2; break;
    default: break;
  }
  digitalWrite(LED_VERDE_PIN, v ? HIGH : LOW);
  digitalWrite(LED_ROJO_PIN, r ? HIGH : LOW);
}

void actualizarPatron() {
  if (otaEnCurso) patron = P_OTA;
  else if (!camaraOk) patron = P_ERROR;
  else if (WiFi.status() != WL_CONNECTED) patron = P_SIN_WIFI;
  else if (!wsConectado) patron = P_SIN_SERVIDOR;
  else if (resultado == "GREEN") patron = P_VERDE;
  else if (resultado == "RED") patron = P_ROJO;
  else patron = P_APAGADO;
}

// ---------------------- CONFIGURACION (NVS) ----------------------

void cargarConfig() {
  prefs.begin("asistencia", false);
  if (!prefs.getBool("init", false)) {
    // Primer arranque: se guardan los valores compilados (pueden ir vacios)
    prefs.putString("servidor", DEF_SERVIDOR);
    prefs.putUShort("puerto", DEF_PUERTO);
    prefs.putUChar("seguridad", DEF_SEGURIDAD);
    prefs.putString("id", DEF_DISPOSITIVO);
    prefs.putString("token", DEF_TOKEN);
    prefs.putBool("init", true);
  }
  cfg.servidor  = prefs.getString("servidor", "");
  cfg.puerto    = prefs.getUShort("puerto", 443);
  cfg.seguridad = prefs.getUChar("seguridad", 2);
  cfg.id        = prefs.getString("id", "");
  cfg.token     = prefs.getString("token", "");
}

bool configCompleta() {
  return cfg.servidor.length() && cfg.id.length() && cfg.token.length() && cfg.puerto > 0;
}

String sufijoMac() {
  String m = WiFi.macAddress();
  m.replace(":", "");
  return m.substring(8);
}

// ---------------------- CAMARA ----------------------

framesize_t tamanoDe(const String &t) {
  if (t == "QVGA") return FRAMESIZE_QVGA;
  if (t == "CIF")  return FRAMESIZE_CIF;
  if (t == "SVGA") return FRAMESIZE_SVGA;
  return FRAMESIZE_VGA;
}

bool inicializarCamara() {
  camera_config_t c;
  c.ledc_channel = LEDC_CHANNEL_0;
  c.ledc_timer   = LEDC_TIMER_0;
  c.pin_d0 = Y2_GPIO_NUM;  c.pin_d1 = Y3_GPIO_NUM;  c.pin_d2 = Y4_GPIO_NUM;  c.pin_d3 = Y5_GPIO_NUM;
  c.pin_d4 = Y6_GPIO_NUM;  c.pin_d5 = Y7_GPIO_NUM;  c.pin_d6 = Y8_GPIO_NUM;  c.pin_d7 = Y9_GPIO_NUM;
  c.pin_xclk     = XCLK_GPIO_NUM;
  c.pin_pclk     = PCLK_GPIO_NUM;
  c.pin_vsync    = VSYNC_GPIO_NUM;
  c.pin_href     = HREF_GPIO_NUM;
  c.pin_sscb_sda = SIOD_GPIO_NUM;
  c.pin_sscb_scl = SIOC_GPIO_NUM;
  c.pin_pwdn     = PWDN_GPIO_NUM;
  c.pin_reset    = RESET_GPIO_NUM;
  // 20 MHz. Con 10 MHz (valor del firmware original) el reloj de la camara mete
  // ruido en el Wi-Fi de la propia placa: en pruebas con ping se perdia el 80-100 %
  // de los paquetes; con 20 MHz el enlace queda igual que sin camara.
  c.xclk_freq_hz = XCLK_CAMARA_HZ;
  c.pixel_format = PIXFORMAT_JPEG;
  // Captura solo cuando se pide una foto. Con GRAB_LATEST la camara captura sin
  // parar en el nucleo 0, el mismo del Wi-Fi, y retrasa los paquetes de red.
  c.grab_mode    = CAMERA_GRAB_WHEN_EMPTY;

  if (psramFound()) {
    // Se reserva para SVGA (el mayor tamano permitido) y luego se baja a VGA:
    // asi el servidor puede cambiar la resolucion sin reiniciar la camara.
    c.frame_size   = FRAMESIZE_SVGA;
    c.jpeg_quality = 12;
    // 2 buferes: mientras se envia una foto, la camara ya captura la siguiente
    // en el otro (bajo demanda: solo llena los buferes libres y se detiene).
    c.fb_count     = 2;
    c.fb_location  = CAMERA_FB_IN_PSRAM;
  } else {
    c.frame_size   = FRAMESIZE_QVGA;
    c.jpeg_quality = 14;
    c.fb_count     = 1;
    c.fb_location  = CAMERA_FB_IN_DRAM;
  }

  esp_err_t err = esp_camera_init(&c);
  if (err != ESP_OK) {
    Serial.printf("Error inicializando la camara: 0x%x\n", err);
    return false;
  }
  sensor_t *s = esp_camera_sensor_get();
  if (psramFound()) s->set_framesize(s, FRAMESIZE_VGA);
  if (s->id.PID == OV3660_PID) {
    s->set_vflip(s, 1);
    s->set_brightness(s, 1);
    s->set_saturation(s, -2);
  }
  return true;
}

// ---------------------- PROTOCOLO CON LA VPS ----------------------

String valorDe(const String &msg, const char *clave) {
  String k = String(clave) + "=";
  int i = msg.indexOf(k);
  if (i < 0) return "";
  i += k.length();
  int j = msg.indexOf(' ', i);
  return j < 0 ? msg.substring(i) : msg.substring(i, j);
}

void aplicarCfg(const String &m) {
  String v = valorDe(m, "intervalo");
  if (v.length()) intervaloMs = constrain(v.toInt(), 50, 5000);
  sensor_t *s = esp_camera_sensor_get();
  if (s && camaraOk) {
    v = valorDe(m, "calidad");
    if (v.length()) s->set_quality(s, constrain(v.toInt(), 10, 40));
    v = valorDe(m, "tam");
    if (v.length() && psramFound()) s->set_framesize(s, tamanoDe(v));
    // Escala de grises (opcional, desactivada por defecto). Solo se toca el
    // sensor si se pide gris: volver a color con set_special_effect(0) no
    // siempre restaura el color del OV2640, asi que en ese caso se reinicia
    // la placa y el sensor arranca a color.
    v = valorDe(m, "gris");
    static bool enGris = false;
    if (v == "1" && !enGris) {
      s->set_special_effect(s, 2);
      enGris = true;
    } else if (v == "0" && enGris) {
      Serial.println("Volviendo a color: reiniciando la placa");
      delay(300);
      ESP.restart();
    }
  }
  // Potencia del Wi-Fi en dBm. A maxima potencia la ESP32-CAM pide picos de
  // corriente que, con una alimentacion justa, danan sus propios paquetes.
  v = valorDe(m, "tx");
  if (v.length()) {
    int q = constrain((int)(v.toFloat() * 4), 8, 78);   // unidades de 0,25 dBm
    WiFi.setTxPower((wifi_power_t)q);
    Serial.printf("Potencia Wi-Fi: %.1f dBm\n", q / 4.0);
  }
  Serial.printf("Config del servidor: intervalo=%lu ms\n", intervaloMs);
}

void enviarHello() {
  String h = "HELLO fw=" FW_VERSION " ip=" + WiFi.localIP().toString() +
             " rssi=" + String(WiFi.RSSI()) + " psram=" + String(psramFound() ? 1 : 0) +
             " heap=" + String(ESP.getFreeHeap()) + " ps=" + String(estadoAhorroWifi()) +
             " mac=" + WiFi.macAddress();
  ws.sendTXT(h);
}

void procesarComando(const String &m) {
  if (m == "GREEN" || m == "RED" || m == "OFF") {
    resultado = m;
    resultadoDesde = millis();
    ws.sendTXT("STATUS:" + m);
  } else if (m == "MODE ACTIVE") {
    modoActivo = true;
    Serial.println("Servidor: transmitir (franja de clase)");
  } else if (m == "MODE SLEEP") {
    modoActivo = false;
    resultado = "OFF";
    Serial.println("Servidor: reposo (fuera de clase)");
  } else if (m.startsWith("CFG ")) {
    aplicarCfg(m);
  } else if (m.startsWith("OTA ")) {
    otaPendiente = m.substring(4);
  } else if (m == "REBOOT") {
    ESP.restart();
  }
}

void eventoWs(WStype_t tipo, uint8_t *payload, size_t largo) {
  switch (tipo) {
    case WStype_CONNECTED:
      wsConectado = true;
      sinServidorDesde = 0;
      modoActivo = false;
      Serial.println("Conectado a la VPS");
      enviarHello();
      if (mensajePendiente.length()) {
        ws.sendTXT(mensajePendiente);
        mensajePendiente = "";
      }
      break;
    case WStype_DISCONNECTED:
      if (wsConectado) {
        Serial.printf("Desconectado de la VPS (%.*s); reintentando...\n",
                      (int)(largo > 80 ? 80 : largo), largo ? (const char *)payload : "sin motivo");
        Serial.printf("  fotos enviadas: %lu, heap libre: %u, RSSI: %d dBm\n",
                      framesEnviados, ESP.getFreeHeap(), WiFi.RSSI());
      }
      wsConectado = false;
      modoActivo = false;
      resultado = "OFF";
      break;
    case WStype_TEXT: {
      char buf[160];
      size_t n = largo < sizeof(buf) - 1 ? largo : sizeof(buf) - 1;
      memcpy(buf, payload, n);
      buf[n] = 0;
      procesarComando(String(buf));
      break;
    }
    case WStype_ERROR:
      Serial.println("Error en el WebSocket");
      break;
    default:
      break;
  }
}

void iniciarWebSocket() {
  const char *host = cfg.servidor.c_str();
  if (cfg.seguridad == 0) {
    ws.begin(host, cfg.puerto, RUTA_WS, "");
  } else if (cfg.seguridad == 2 && strlen(CA_BUNDLE) > 0) {
    ws.beginSslWithCA(host, cfg.puerto, RUTA_WS, CA_BUNDLE, "");
  } else {
    if (cfg.seguridad == 2) Serial.println("AVISO: firmware sin certificados CA; TLS sin verificar");
    ws.beginSSL(host, cfg.puerto, RUTA_WS, "", "");
  }
  headersWs = "Authorization: Bearer " + cfg.token + "\r\nX-Dispositivo: " + cfg.id +
              "\r\nX-Firmware: " FW_VERSION;
  ws.setExtraHeaders(headersWs.c_str());
  ws.onEvent(eventoWs);
  ws.setReconnectInterval(WS_REINTENTO_MS);
  // Ping cada 15 s; reconecta tras 3 pings sin respuesta (10 s cada uno). Margen
  // amplio: con Wi-Fi inestable y la VPS lejos, un bache de unos segundos es normal.
  // El limite de escritura de la libreria (5 s de fabrica) se sube a 20 s en
  // build_opt.h, para que una foto lenta no corte la conexion.
  ws.enableHeartbeat(15000, 10000, 3);
  Serial.printf("Servidor: %s://%s:%u%s como '%s'\n", cfg.seguridad ? "wss" : "ws",
                host, cfg.puerto, RUTA_WS, cfg.id.c_str());
}

// ---------------------- OTA (actualizacion por Wi-Fi) ----------------------

void ejecutarOTA(const String &version) {
  Serial.printf("Actualizando firmware %s -> %s\n", FW_VERSION, version.c_str());
  otaEnCurso = true;
  actualizarPatron();
  modoActivo = false;
  ws.disconnect();
  wsConectado = false;
  delay(300);

  WiFiClient plano;
  WiFiClientSecure seguro;
  WiFiClient *cliente = &plano;
  if (cfg.seguridad > 0) {
    if (cfg.seguridad == 2 && strlen(CA_BUNDLE) > 0) seguro.setCACert(CA_BUNDLE);
    else seguro.setInsecure();
    cliente = &seguro;
  }
  String url = String(cfg.seguridad ? "https://" : "http://") + cfg.servidor + ":" + cfg.puerto +
               "/api/dispositivo/firmware";
  HTTPClient http;
  http.setTimeout(30000);
  String error = "";
  if (!http.begin(*cliente, url)) {
    error = "no se pudo iniciar la descarga";
  } else {
    http.addHeader("Authorization", "Bearer " + cfg.token);
    http.addHeader("X-Dispositivo", cfg.id);
    int codigo = http.GET();
    int largo = http.getSize();
    if (codigo != 200) {
      error = "HTTP " + String(codigo);
    } else if (!Update.begin(largo > 0 ? largo : UPDATE_SIZE_UNKNOWN)) {
      error = "sin espacio OTA (compilar con particion min_spiffs)";
    } else {
      Update.onProgress([](size_t, size_t) { esp_task_wdt_reset(); });
      size_t escritos = Update.writeStream(*http.getStreamPtr());
      if (largo > 0 && (int)escritos != largo) error = "descarga incompleta";
      else if (!Update.end(true)) error = "imagen invalida (" + String(Update.errorString()) + ")";
    }
    http.end();
  }
  if (error.length() == 0) {
    Serial.println("Firmware actualizado. Reiniciando...");
    delay(500);
    ESP.restart();
  }
  Update.abort();
  Serial.println("OTA fallida: " + error);
  mensajePendiente = "OTA:ERROR " + error;
  otaEnCurso = false;
  iniciarWebSocket();
}

// ---------------------- WI-FI Y PORTAL CAUTIVO ----------------------

bool paramsGuardados = false;
void alGuardarParams() { paramsGuardados = true; }

void conectarWifi(bool forzarPortal) {
  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);   // menos latencia y mas ancho de banda para el video

  // Wi-Fi precargado al compilar (opcional): se aplica una sola vez
  if (strlen(DEF_WIFI_SSID) > 0 && !prefs.getBool("wifiDef", false)) {
    prefs.putBool("wifiDef", true);
    Serial.printf("Probando Wi-Fi precargado '%s'\n", DEF_WIFI_SSID);
    WiFi.begin(DEF_WIFI_SSID, DEF_WIFI_CLAVE);
    for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) delay(500);
  }
  if (!forzarPortal && WiFi.status() == WL_CONNECTED) return;

  char puertoTxt[6], segTxt[2];
  snprintf(puertoTxt, sizeof(puertoTxt), "%u", cfg.puerto);
  snprintf(segTxt, sizeof(segTxt), "%u", cfg.seguridad);
  WiFiManagerParameter pServidor("servidor", "Servidor (dominio de la VPS, ej. 45-12-34-56.sslip.io)", cfg.servidor.c_str(), 80);
  WiFiManagerParameter pPuerto("puerto", "Puerto (443)", puertoTxt, 5);
  WiFiManagerParameter pSeg("seguridad", "Seguridad: 2 = HTTPS verificado (recomendado), 1 = HTTPS sin verificar, 0 = sin cifrar", segTxt, 1);
  WiFiManagerParameter pId("dispositivo", "Identificador de la camara (igual al del panel)", cfg.id.c_str(), 64);
  WiFiManagerParameter pToken("token", "Token de la camara (se genera en el panel)", cfg.token.c_str(), 100);

  WiFiManager wm;
  wm.addParameter(&pServidor);
  wm.addParameter(&pPuerto);
  wm.addParameter(&pSeg);
  wm.addParameter(&pId);
  wm.addParameter(&pToken);
  wm.setSaveParamsCallback(alGuardarParams);
  wm.setBreakAfterConfig(true);
  wm.setConfigPortalTimeout(PORTAL_TIMEOUT_S);
  wm.setConnectTimeout(20);
  wm.setTitle("Asistencia facial");
  wm.setAPCallback([](WiFiManager *) {
    patron = P_PORTAL;
    Serial.println("Portal de configuracion abierto: conectate a la red ASISTENCIA-... y abre 192.168.4.1");
  });

  String ap = "ASISTENCIA-" + sufijoMac();
  bool ok = forzarPortal ? wm.startConfigPortal(ap.c_str(), DEF_PORTAL_CLAVE)
                         : wm.autoConnect(ap.c_str(), DEF_PORTAL_CLAVE);

  if (paramsGuardados) {
    String s = String(pServidor.getValue());
    s.trim();
    s.replace("https://", "");
    s.replace("http://", "");
    if (s.endsWith("/")) s.remove(s.length() - 1);
    String tk = String(pToken.getValue());
    tk.trim();
    String id = String(pId.getValue());
    id.trim();
    prefs.putString("servidor", s);
    prefs.putUShort("puerto", (uint16_t)constrain(atoi(pPuerto.getValue()), 1, 65535));
    prefs.putUChar("seguridad", (uint8_t)constrain(atoi(pSeg.getValue()), 0, 2));
    prefs.putString("id", id);
    prefs.putString("token", tk);
    Serial.println("Configuracion guardada.");
    cargarConfig();
  }
  if (!ok || WiFi.status() != WL_CONNECTED) {
    Serial.println("Sin Wi-Fi tras el portal. Reiniciando para reintentar...");
    delay(1000);
    ESP.restart();
  }
}

// Milisegundos desde que se capturo la foto (la marca de tiempo cuenta desde el arranque).
int64_t antiguedadFotoMs(const camera_fb_t *fb) {
  int64_t capturada = (int64_t)fb->timestamp.tv_sec * 1000000LL + fb->timestamp.tv_usec;
  return (esp_timer_get_time() - capturada) / 1000;
}

// ---------------------- WI-FI SIN AHORRO DE ENERGIA ----------------------

// Con el ahorro de energia ("modem sleep") la radio duerme entre balizas del
// router: cada paquete que llega a la camara espera hasta 100-300 ms, se pierden
// confirmaciones TCP y el video por WSS se cae. Se fuerza a apagado al conectar
// y se revisa cada minuto. El estado se informa al servidor (ps=0 es lo correcto).
int estadoAhorroWifi() {
  wifi_ps_type_t ps;
  return esp_wifi_get_ps(&ps) == ESP_OK ? (int)ps : -1;
}

void sinAhorroWifi() {
  int antes = estadoAhorroWifi();
  if (antes != WIFI_PS_NONE) {
    WiFi.setSleep(false);
    esp_wifi_set_ps(WIFI_PS_NONE);
  }
  Serial.printf("Ahorro de energia Wi-Fi: estaba en %d, ahora %d (0 = desactivado)\n",
                antes, estadoAhorroWifi());
}

// ---------------------- BOTON ----------------------

// Igual que el firmware original: un click (ALTO) apaga los LED. El boton NO
// reinicia ni abre el portal: con el Wi-Fi transmitiendo, el cable de IO13
// capta ruido y la placa veia pulsaciones largas que nadie hacia.
// Filtros contra ese ruido:
//   - la pulsacion cuenta solo si el pin queda en ALTO al menos DEBOUNCE_BOTON_MS;
//   - solo actua si hay un LED de resultado encendido;
//   - si llegan demasiadas pulsaciones seguidas, el boton se desactiva hasta reiniciar.
bool botonDesactivado = false;

void revisarBoton() {
  static bool presionado = false;
  static unsigned long desde = 0;
  static unsigned long ventana = 0;
  static uint8_t pulsaciones = 0;
  if (botonDesactivado) return;
  bool alto = digitalRead(BOTON_PIN) == HIGH;
  unsigned long ahora = millis();
  if (alto && !presionado) {
    presionado = true;
    desde = ahora;
  } else if (!alto && presionado) {
    presionado = false;
    if (ahora - desde < DEBOUNCE_BOTON_MS) return;   // pico de ruido
    if (ahora - ventana > 10000) {
      ventana = ahora;
      pulsaciones = 0;
    }
    if (++pulsaciones > 6) {
      botonDesactivado = true;
      Serial.println("AVISO: el boton (IO13) recibe pulsaciones falsas por ruido; se desactiva hasta reiniciar.");
      return;
    }
    if (resultado != "OFF") {
      Serial.println("Boton: reiniciando estado de los LED");
      resultado = "OFF";
      if (wsConectado) ws.sendTXT("STATUS:OFF");
    }
  }
}

// ---------------------- SETUP ----------------------

void setup() {
  Serial.begin(115200);
  Serial.println();
  Serial.println("Asistencia facial ESP32-CAM - firmware " FW_VERSION);

  pinMode(FLASH_LED_PIN, OUTPUT);
  digitalWrite(FLASH_LED_PIN, LOW);
  pinMode(LED_VERDE_PIN, OUTPUT);
  pinMode(LED_ROJO_PIN, OUTPUT);
  // La resistencia interna a GND (debil, ~45k) evita pulsaciones fantasma si el
  // pin queda al aire; una resistencia externa del montaje siempre la domina.
  pinMode(BOTON_PIN, INPUT_PULLDOWN);
  tickerLed.attach_ms(100, animarLeds);

  cargarConfig();

  camaraOk = inicializarCamara();
  if (!camaraOk) {
    patron = P_ERROR;
    Serial.println("No se pudo iniciar la camara. Revisa el cable plano. Reiniciando en 10 s...");
    delay(10000);
    ESP.restart();
  }

  bool forzarPortal = prefs.getBool("portal", false) || !configCompleta();
  if (forzarPortal) {
    prefs.putBool("portal", false);
    if (!configCompleta()) Serial.println("Falta configurar servidor/identificador/token: abriendo portal");
  }
  patron = P_SIN_WIFI;
  conectarWifi(forzarPortal);
  WiFi.setAutoReconnect(true);
  Serial.print("Wi-Fi conectado. IP: ");
  Serial.println(WiFi.localIP());
  sinAhorroWifi();

  configTime(-5 * 3600, 0, "pool.ntp.org", "time.google.com");   // hora de Colombia (logs)

  esp_task_wdt_init(WDT_SEGUNDOS, true);   // si el programa se cuelga, la placa se reinicia
  esp_task_wdt_add(NULL);

  iniciarWebSocket();
}

// ---------------------- LOOP ----------------------

void loop() {
  esp_task_wdt_reset();
  unsigned long ahora = millis();

  ws.loop();
  revisarBoton();

  // Vigilancia del Wi-Fi
  if (WiFi.status() != WL_CONNECTED) {
    if (!sinWifiDesde) sinWifiDesde = ahora;
    if (ahora - ultimoReintentoWifi > WIFI_REINTENTO_MS) {
      ultimoReintentoWifi = ahora;
      WiFi.reconnect();
    }
    if (ahora - sinWifiDesde > REINICIO_SIN_WIFI_MS) {
      Serial.println("Sin Wi-Fi por 3 minutos: reiniciando");
      ESP.restart();
    }
  } else {
    sinWifiDesde = 0;
  }

  // Vigilancia de la conexion con la VPS
  if (!wsConectado) {
    if (!sinServidorDesde) sinServidorDesde = ahora;
    if (WiFi.status() == WL_CONNECTED && ahora - sinServidorDesde > REINICIO_SIN_SERVIDOR_MS) {
      Serial.println("Sin conexion con la VPS por 10 minutos: reiniciando");
      ESP.restart();
    }
  }

  // Seguridad: el LED de resultado no queda encendido indefinidamente
  if (resultado != "OFF" && ahora - resultadoDesde > LED_RESULTADO_MAX_MS) resultado = "OFF";

  if (otaPendiente.length()) {
    String v = otaPendiente;
    otaPendiente = "";
    ejecutarOTA(v);
  }

  // Envio de fotogramas solo cuando el servidor lo pide
  if (wsConectado && modoActivo && camaraOk && ahora - ultimoFrame >= intervaloMs) {
    ultimoFrame = ahora;
    // La foto ya suele estar lista (se capturo durante el envio anterior), asi
    // que no hay espera. Solo se descartan las viejas, p. ej. al empezar a
    // transmitir tras el reposo (los 2 buferes guardan fotos de hace rato).
    camera_fb_t *fb = esp_camera_fb_get();
    for (int i = 0; fb && i < 2 && antiguedadFotoMs(fb) > FOTO_MAX_ANTIGUEDAD_MS; i++) {
      esp_camera_fb_return(fb);
      fb = esp_camera_fb_get();
    }
    if (fb) {
      unsigned long t0 = millis();
      bool ok = ws.sendBIN(fb->buf, fb->len);
      unsigned long ms = millis() - t0;
      if (ok) framesEnviados++;
      // Diagnostico: las primeras fotos, cada 50 y cualquier fallo
      if (!ok || framesEnviados <= 5 || framesEnviados % 50 == 0) {
        Serial.printf("Foto %s: %u KB en %lu ms (enviadas %lu, heap %u, RSSI %d)\n",
                      ok ? "ok" : "FALLO", fb->len / 1024, ms, framesEnviados,
                      ESP.getFreeHeap(), WiFi.RSSI());
      }
      esp_camera_fb_return(fb);
    }
  }

  if (wsConectado && ahora - ultimasEstadisticas > ESTADISTICAS_MS) {
    ultimasEstadisticas = ahora;
    if (estadoAhorroWifi() != WIFI_PS_NONE) sinAhorroWifi();
    ws.sendTXT("STAT rssi=" + String(WiFi.RSSI()) + " heap=" + String(ESP.getFreeHeap()) +
               " up=" + String(ahora / 1000) + " frames=" + String(framesEnviados) +
               " ps=" + String(estadoAhorroWifi()));
  }

  actualizarPatron();
  delay(1);
}
