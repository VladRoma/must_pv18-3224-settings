/*
 * ESP32-CAM (без камери) + DHT11 → Wi‑Fi → Raspberry Pi
 * POST http://<PI>:8080/api/bathroom/ingest
 *
 * Бібліотеки Arduino IDE: DHT sensor library (Adafruit), WiFi, HTTPClient
 * Файл config.h — з config.example.h
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <DHT.h>
#include "config.h"

DHT dht(DHT_PIN, DHT11);

void setup() {
  Serial.begin(115200);
  delay(300);
  dht.begin();

  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  Serial.print("WiFi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }
  Serial.println();
  Serial.print("IP ESP32: ");
  Serial.println(WiFi.localIP());
}

bool postReading(float tempC, float humPct) {
  if (WiFi.status() != WL_CONNECTED) {
    return false;
  }

  HTTPClient http;
  String url = String("http://") + PI_HOST + ":" + String(PI_PORT) + "/api/bathroom/ingest";
  http.begin(url);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Bathroom-Token", INGEST_TOKEN);

  String body = "{";
  body += "\"temperature_c\":" + String(tempC, 1) + ",";
  body += "\"humidity_pct\":" + String(humPct, 1) + ",";
  body += "\"device\":\"bathroom-esp32\"";
  body += "}";

  int code = http.POST(body);
  Serial.printf("POST %d  T=%.1f  H=%.0f\n", code, tempC, humPct);
  http.end();
  return code >= 200 && code < 300;
}

void loop() {
  float h = dht.readHumidity();
  float t = dht.readTemperature();

  if (isnan(h) || isnan(t)) {
    Serial.println("DHT11 read failed");
    delay(5000);
    return;
  }

  postReading(t, h);
  delay(POST_INTERVAL_MS);
}
