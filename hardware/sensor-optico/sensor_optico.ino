#include <WiFi.h>
#include <WebServer.h>
#include <ArduinoOTA.h>

//WIFI

const char* ssid = "NOMBRE_WIFI";
const char* password = "PASSWORD_WIFI";

//OTA

const char* otaHostname = "esp32-sensor-optico";
const char* otaPassword = "PASSWORD_OTA";

//PINES

const int relePin = 26;     // Rele que controla el laser
const int sensorPin = 32;   // Fotodiodo BPW34

const int LASER_ON = HIGH;
const int LASER_OFF = LOW;

//MEDICION

const unsigned long TIEMPO_FASE_MS = 120000;     
const unsigned long INTERVALO_LECTURA_MS = 200; 

//SERVIDOR

WebServer server(80);

//ESTRUCTURAS

struct ResultadoMedicion {
  unsigned long numMuestras;
  unsigned long sumaADC;
  float mediaADC;
  int minimoADC;
  int maximoADC;
  int rangoADC;
};

enum FaseMedicion {
  REPOSO,
  LASER_APAGADO,
  LASER_ENCENDIDO
};

//VARIABLES GLOBALES

ResultadoMedicion resultadoLaserOff;
ResultadoMedicion resultadoLaserOn;

FaseMedicion faseActual = REPOSO;

bool midiendo = false;

String estado = "Sistema en reposo. Laser apagado.";

float diferenciaMediaADC = 0.0;
float diferenciaLuzInvertida = 0.0;

unsigned long inicioFaseMs = 0;
unsigned long ultimaLecturaMs = 0;
unsigned long ultimaMedicionMs = 0;

//FUNCIONES AUXILIARES

void resetResultado(ResultadoMedicion &r) {
  /*
  Reinicia los valores acumulados de una medición antes de empezar una nueva fase.
  */
  r.numMuestras = 0;
  r.sumaADC = 0;
  r.mediaADC = 0.0;
  r.minimoADC = 4095;
  r.maximoADC = 0;
  r.rangoADC = 0;
}

void agregarLectura(ResultadoMedicion &r, int valorADC) {
  /*
  Añade una lectura ADC al resultado de la fase actual y actualiza sus valores mínimo y máximo.
  */
  r.sumaADC += valorADC;
  r.numMuestras++;

  if (valorADC < r.minimoADC) {
    r.minimoADC = valorADC;
  }

  if (valorADC > r.maximoADC) {
    r.maximoADC = valorADC;
  }
}

void cerrarResultado(ResultadoMedicion &r) {
  /*
  Calcula la media y el rango final de una fase de medición.
  */
  if (r.numMuestras > 0) {
    r.mediaADC = (float)r.sumaADC / (float)r.numMuestras;
    r.rangoADC = r.maximoADC - r.minimoADC;
  } else {
    r.mediaADC = 0.0;
    r.minimoADC = 0;
    r.maximoADC = 0;
    r.rangoADC = 0;
  }
}

void iniciarMedicion() {
  /*
  Inicializa una medición completa del sensor óptico comenzando por la fase con el láser apagado.
  */
  resetResultado(resultadoLaserOff);
  resetResultado(resultadoLaserOn);

  diferenciaMediaADC = 0.0;
  diferenciaLuzInvertida = 0.0;

  midiendo = true;
  faseActual = LASER_APAGADO;

  digitalWrite(relePin, LASER_OFF);

  inicioFaseMs = millis();
  ultimaLecturaMs = 0;

  estado = "Medicion en curso: laser apagado.";

  Serial.println();
  Serial.println("======================================");
  Serial.println("INICIO MEDICION COMPLETA SENSOR OPTICO");
  Serial.println("Fase 1: laser apagado");
  Serial.println("======================================");
}

void finalizarMedicion() {
  /*
  Finaliza la medición completa, calcula las diferencias entre fases y deja el láser apagado.
  */
  digitalWrite(relePin, LASER_OFF);

  cerrarResultado(resultadoLaserOff);
  cerrarResultado(resultadoLaserOn);

  diferenciaMediaADC = resultadoLaserOff.mediaADC - resultadoLaserOn.mediaADC;

  float luzInvertidaOff = 4095.0 - resultadoLaserOff.mediaADC;
  float luzInvertidaOn = 4095.0 - resultadoLaserOn.mediaADC;

  diferenciaLuzInvertida = luzInvertidaOn - luzInvertidaOff;

  ultimaMedicionMs = millis();

  midiendo = false;
  faseActual = REPOSO;

  estado = "Medicion terminada. Laser apagado.";

  Serial.println("======================================");
  Serial.println("MEDICION COMPLETA TERMINADA");
  Serial.print("Media laser apagado: ");
  Serial.println(resultadoLaserOff.mediaADC, 2);
  Serial.print("Media laser encendido: ");
  Serial.println(resultadoLaserOn.mediaADC, 2);
  Serial.print("Diferencia ADC OFF - ON: ");
  Serial.println(diferenciaMediaADC, 2);
  Serial.print("Diferencia luz invertida ON - OFF: ");
  Serial.println(diferenciaLuzInvertida, 2);
  Serial.println("======================================");
}

void actualizarMedicion() {
  /*
  Controla el avance de la medición, toma lecturas periódicas y cambia entre fases.
  */
  if (!midiendo) {
    return;
  }

  unsigned long ahora = millis();

  if (ahora - ultimaLecturaMs >= INTERVALO_LECTURA_MS) {
    ultimaLecturaMs = ahora;

    int valorADC = analogRead(sensorPin);

    if (faseActual == LASER_APAGADO) {
      agregarLectura(resultadoLaserOff, valorADC);
    } else if (faseActual == LASER_ENCENDIDO) {
      agregarLectura(resultadoLaserOn, valorADC);
    }
  }

  if (ahora - inicioFaseMs >= TIEMPO_FASE_MS) {
    if (faseActual == LASER_APAGADO) {
      cerrarResultado(resultadoLaserOff);

      faseActual = LASER_ENCENDIDO;
      digitalWrite(relePin, LASER_ON);

      inicioFaseMs = millis();
      ultimaLecturaMs = 0;

      estado = "Medicion en curso: laser encendido.";

      Serial.println("Fase 1 terminada.");
      Serial.println("Fase 2: laser encendido");
    } else if (faseActual == LASER_ENCENDIDO) {
      finalizarMedicion();
    }
  }
}

//JSON

String resultadoAJSON(ResultadoMedicion r) {
  /*
  Convierte los datos de una fase de medición en una cadena JSON.
  */
  String json = "{";

  json += "\"num_muestras\":" + String(r.numMuestras) + ",";
  json += "\"media_adc\":" + String(r.mediaADC, 2) + ",";
  json += "\"min_adc\":" + String(r.minimoADC) + ",";
  json += "\"max_adc\":" + String(r.maximoADC) + ",";
  json += "\"rango_adc\":" + String(r.rangoADC);

  json += "}";

  return json;
}

String generarJSONResultado() {
  /*
  Genera el JSON completo con el estado del sistema y los resultados de la última medición.
  */
  String json = "{";

  json += "\"estado\":\"" + estado + "\",";
  json += "\"midiendo\":";
  json += midiendo ? "true" : "false";
  json += ",";

  json += "\"ip\":\"" + WiFi.localIP().toString() + "\",";
  json += "\"mac\":\"" + WiFi.macAddress() + "\",";
  json += "\"ota_hostname\":\"" + String(otaHostname) + "\",";
  json += "\"uptime_ms\":" + String(millis()) + ",";

  json += "\"sensor_pin\":" + String(sensorPin) + ",";
  json += "\"adc_resolution\":12,";
  json += "\"adc_max\":4095,";

  json += "\"tiempo_laser_off_ms\":" + String(TIEMPO_FASE_MS) + ",";
  json += "\"tiempo_laser_on_ms\":" + String(TIEMPO_FASE_MS) + ",";
  json += "\"intervalo_lectura_ms\":" + String(INTERVALO_LECTURA_MS) + ",";

  json += "\"laser_off\":";
  json += resultadoAJSON(resultadoLaserOff);
  json += ",";

  json += "\"laser_on\":";
  json += resultadoAJSON(resultadoLaserOn);
  json += ",";

  json += "\"media_adc_laser_off\":" + String(resultadoLaserOff.mediaADC, 2) + ",";
  json += "\"media_adc_laser_on\":" + String(resultadoLaserOn.mediaADC, 2) + ",";
  json += "\"diferencia_media_adc_off_menos_on\":" + String(diferenciaMediaADC, 2) + ",";
  json += "\"diferencia_luz_invertida_on_menos_off\":" + String(diferenciaLuzInvertida, 2);

  json += "}";

  return json;
}

String generarJSONEstado() {
  /*
  Genera un JSON reducido con el estado actual del sistema y los datos básicos de conexión.
  */
  String json = "{";

  json += "\"estado\":\"" + estado + "\",";
  json += "\"midiendo\":";
  json += midiendo ? "true" : "false";
  json += ",";

  json += "\"ip\":\"" + WiFi.localIP().toString() + "\",";
  json += "\"mac\":\"" + WiFi.macAddress() + "\",";
  json += "\"ota_hostname\":\"" + String(otaHostname) + "\",";
  json += "\"uptime_ms\":" + String(millis()) + ",";
  json += "\"laser_reposo\":\"off\"";

  json += "}";

  return json;
}

//PAGINA WEB

String paginaHTML() {
  /*
  Construye la página web de control y visualización del sensor óptico.
  */
  String html = "";

  html += "<!DOCTYPE html><html><head>";
  html += "<meta charset='UTF-8'>";
  html += "<meta name='viewport' content='width=device-width, initial-scale=1.0'>";
  html += "<meta http-equiv='refresh' content='5'>";
  html += "<title>Sensor optico de suciedad</title>";

  html += "<style>";
  html += "body{font-family:Arial;margin:30px;background:#f5f5f5;color:#222;}";
  html += ".card{background:white;padding:20px;border-radius:12px;max-width:800px;box-shadow:0 2px 8px #ccc;}";
  html += "h1{font-size:24px;}";
  html += ".estado{font-size:18px;font-weight:bold;color:green;}";
  html += ".dato{font-size:17px;margin:8px 0;}";
  html += "button{display:inline-block;margin:10px 10px 0 0;padding:12px 16px;border:none;border-radius:8px;background:#006dcc;color:white;font-weight:bold;font-size:16px;cursor:pointer;}";
  html += "button:disabled{background:#888;cursor:not-allowed;}";
  html += "a{display:inline-block;margin:10px 10px 0 0;color:#0066cc;font-weight:bold;}";
  html += "</style>";

  html += "<script>";
  html += "function ejecutarMedicion(){";
  html += "var b=document.getElementById('botonMedir');";
  html += "b.disabled=true;";
  html += "b.innerText='Midiendo...';";
  html += "fetch('/medir').then(function(){setTimeout(function(){location.reload();},1000);});";
  html += "}";
  html += "</script>";

  html += "</head><body>";
  html += "<div class='card'>";

  html += "<h1>Sensor optico de suciedad</h1>";

  html += "<p><b>Estado:</b> <span class='estado'>" + estado + "</span></p>";
  html += "<p><b>IP ESP32:</b> " + WiFi.localIP().toString() + "</p>";
  html += "<p><b>MAC ESP32:</b> " + WiFi.macAddress() + "</p>";
  html += "<p><b>Nombre OTA:</b> " + String(otaHostname) + "</p>";

  html += "<hr>";

  html += "<h2>Ultima medicion</h2>";
  html += "<p class='dato'><b>Media laser apagado:</b> " + String(resultadoLaserOff.mediaADC, 2) + "</p>";
  html += "<p class='dato'><b>Media laser encendido:</b> " + String(resultadoLaserOn.mediaADC, 2) + "</p>";
  html += "<p class='dato'><b>Diferencia ADC OFF - ON:</b> " + String(diferenciaMediaADC, 2) + "</p>";
  html += "<p class='dato'><b>Diferencia luz invertida ON - OFF:</b> " + String(diferenciaLuzInvertida, 2) + "</p>";

  html += "<hr>";

  html += "<p>La medicion completa dura 4 minutos: 2 minutos con laser apagado y 2 minutos con laser encendido.</p>";
  html += "<p>Durante la medicion, la pagina sigue funcionando. Solo se bloquea el boton de nueva medicion.</p>";

  if (midiendo) {
    html += "<button id='botonMedir' disabled>Midiendo...</button>";
  } else {
    html += "<button id='botonMedir' onclick='ejecutarMedicion()'>Ejecutar medicion ahora</button>";
  }

  html += "<a href='/estado'>Ver estado JSON</a>";
  html += "<a href='/resultado'>Ver resultado JSON</a>";

  html += "</div></body></html>";

  return html;
}

//HANDLERS

void handleRoot() {
  /*
  Atiende la ruta principal del servidor y devuelve la página HTML.
  */
  server.send(200, "text/html", paginaHTML());
}

void handleEstado() {
  /*
  Atiende la ruta de estado y devuelve la información básica del sistema en JSON.
  */
  server.send(200, "application/json", generarJSONEstado());
}

void handleResultado() {
  /*
  Atiende la ruta de resultado y devuelve la última medición completa en JSON.
  */
  server.send(200, "application/json", generarJSONResultado());
}

void handleMedir() {
  /*
  Atiende la petición de nueva medición e impide iniciar otra si ya hay una en curso.
  */
  if (midiendo) {
    server.send(409, "application/json", "{\"error\":\"Ya hay una medicion en curso\"}");
    return;
  }

  iniciarMedicion();

  server.send(202, "application/json", "{\"ok\":true,\"mensaje\":\"Medicion iniciada\"}");
}

//OTA

void configurarOTA() {
  /*
  Configura la actualización remota OTA y sus eventos de inicio, progreso, finalización y error.
  */
  ArduinoOTA.setHostname(otaHostname);
  ArduinoOTA.setPassword(otaPassword);

  ArduinoOTA.onStart([]() {
    digitalWrite(relePin, LASER_OFF);
    estado = "Actualizacion OTA iniciada. Laser apagado por seguridad.";
    Serial.println("Actualizacion OTA iniciada");
  });

  ArduinoOTA.onEnd([]() {
    digitalWrite(relePin, LASER_OFF);
    estado = "Actualizacion OTA terminada. Laser apagado.";
    Serial.println("Actualizacion OTA terminada");
  });

  ArduinoOTA.onProgress([](unsigned int progress, unsigned int total) {
    int porcentaje = (progress * 100) / total;
    Serial.printf("Progreso OTA: %u%%\r", porcentaje);
  });

  ArduinoOTA.onError([](ota_error_t error) {
    digitalWrite(relePin, LASER_OFF);

    Serial.printf("Error OTA [%u]: ", error);

    if (error == OTA_AUTH_ERROR) {
      Serial.println("Error de autenticacion");
      estado = "Error OTA: autenticacion";
    } else if (error == OTA_BEGIN_ERROR) {
      Serial.println("Error al comenzar");
      estado = "Error OTA: comienzo";
    } else if (error == OTA_CONNECT_ERROR) {
      Serial.println("Error de conexion");
      estado = "Error OTA: conexion";
    } else if (error == OTA_RECEIVE_ERROR) {
      Serial.println("Error de recepcion");
      estado = "Error OTA: recepcion";
    } else if (error == OTA_END_ERROR) {
      Serial.println("Error al finalizar");
      estado = "Error OTA: finalizacion";
    }
  });

  ArduinoOTA.begin();

  Serial.println("OTA preparado.");
  Serial.print("Nombre OTA: ");
  Serial.println(otaHostname);
}

//SETUP

void setup() {
  /*
  Inicializa el hardware, la conexión WiFi, el servidor web, las rutas HTTP y el servicio OTA.
  */
  Serial.begin(115200);

  pinMode(relePin, OUTPUT);
  digitalWrite(relePin, LASER_OFF);

  analogReadResolution(12);
  analogSetPinAttenuation(sensorPin, ADC_11db);

  resetResultado(resultadoLaserOff);
  resetResultado(resultadoLaserOn);

  WiFi.begin(ssid, password);

  Serial.println("Conectando a WiFi...");

  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println();
  Serial.println("WiFi conectado.");
  Serial.print("IP de la ESP32: ");
  Serial.println(WiFi.localIP());
  Serial.print("MAC de la ESP32: ");
  Serial.println(WiFi.macAddress());

  configurarOTA();

  server.on("/", HTTP_GET, handleRoot);
  server.on("/estado", HTTP_GET, handleEstado);
  server.on("/resultado", HTTP_GET, handleResultado);
  server.on("/medir", HTTP_GET, handleMedir);
  server.on("/medir", HTTP_POST, handleMedir);

  server.begin();

  estado = "Sistema en reposo. Laser apagado.";

  Serial.println("Servidor iniciado.");
  Serial.println("Rutas disponibles:");
  Serial.println("/");
  Serial.println("/estado");
  Serial.println("/resultado");
  Serial.println("/medir");
}

//LOOP

void loop() {
  /*
  Mantiene activos el servidor web, el servicio OTA y el proceso de medición del sensor.
  */
  server.handleClient();
  ArduinoOTA.handle();
  actualizarMedicion();
}