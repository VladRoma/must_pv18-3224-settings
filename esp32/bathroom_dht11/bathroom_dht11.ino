/*
 * ESP32-CAM (без камери) + DHT11 → Wi‑Fi → Raspberry Pi
 * POST http://<PI>:8080/api/bathroom/ingest
 *
 * Бібліотеки: DHT sensor library (Adafruit), WiFi, HTTPClient
 * config.h — з config.example.h
 */

#include <WiFi.h>
#include <HTTPClient.h>
#include <DHT.h>
#include "config.h"

#ifndef HTTP_TIMEOUT_MS
#define HTTP_TIMEOUT_MS 8000
#endif
#ifndef POST_RETRY_COUNT
#define POST_RETRY_COUNT 3
#endif
#ifndef POST_RETRY_DELAY_MS
#define POST_RETRY_DELAY_MS 2000
#endif
#ifndef DHT_WARMUP_MS
#define DHT_WARMUP_MS 2000
#endif
#ifndef DHT_READ_RETRY
#define DHT_READ_RETRY 3
#endif
#ifndef WIFI_CONNECT_TIMEOUT_MS
#define WIFI_CONNECT_TIMEOUT_MS 30000
#endif

DHT dht(DHT_PIN, DHT11);

bool connectWifi(uint32_t timeoutMs) {
  if (WiFi.status() == WL_CONNECTED) {
    return true;
  }

  Serial.println("WiFi reconnect...");
  WiFi.mode(WIFI_STA);
  WiFi.disconnect();
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);

  uint32_t start = millis();
  while (WiFi.status() != WL_CONNECTED && (millis() - start) < timeoutMs) {
    delay(250);
    Serial.print('.');
  }
  Serial.println();

  if (WiFi.status() == WL_CONNECTED) {
    Serial.print("IP ESP32: ");
    Serial.println(WiFi.localIP());
    return true;
  }

  Serial.println("WiFi: не вдалося підключитися");
  return false;
}

bool readDht(float &tempC, float &humPct) {
  for (int attempt = 0; attempt < DHT_READ_RETRY; attempt++) {
    humPct = dht.readHumidity();
    tempC = dht.readTemperature();
    if (!isnan(humPct) && !isnan(tempC)) {
      return true;
    }
    delay(500);
  }
  return false;
}

int postReadingOnce(float tempC, float humPct) {
  HTTPClient http;
  String url = String("http://") + PI_HOST + ":" + String(PI_PORT) + "/api/bathroom/ingest";
  http.begin(url);
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.addHeader("Content-Type", "application/json");
  http.addHeader("X-Bathroom-Token", INGEST_TOKEN);

  String body = "{";
  body += "\"temperature_c\":" + String(tempC, 1) + ",";
  body += "\"humidity_pct\":" + String(humPct, 1) + ",";
  body += "\"device\":\"bathroom-esp32\"";
  body += "}";

  int code = http.POST(body);
  http.end();
  return code;
}

bool postReading(float tempC, float humPct) {
  for (int i = 0; i < POST_RETRY_COUNT; i++) {
    if (!connectWifi(WIFI_CONNECT_TIMEOUT_MS)) {
      delay(POST_RETRY_DELAY_MS);
      continue;
    }

    int code = postReadingOnce(tempC, humPct);
    Serial.printf("POST %d  T=%.1f  H=%.0f\n", code, tempC, humPct);

    if (code >= 200 && code < 300) {
      return true;
    }

    if (code == 401) {
      Serial.println("POST 401 — INGEST_TOKEN не збігається з BATHROOM_INGEST_TOKEN на Pi");
      return false;
    }

    if (i + 1 < POST_RETRY_COUNT) {
      Serial.printf("POST retry %d/%d через %d ms\n", i + 2, POST_RETRY_COUNT, POST_RETRY_DELAY_MS);
      delay(POST_RETRY_DELAY_MS);
    }
  }
  return false;
}

void setup() {
  Serial.begin(115200);
  delay(300);
  Serial.println();
  Serial.println("ESP32 bathroom DHT11 → MUST Pi");

  dht.begin();
  delay(DHT_WARMUP_MS);

  connectWifi(WIFI_CONNECT_TIMEOUT_MS);
}

void loop() {
  if (WiFi.status() != WL_CONNECTED) {
    connectWifi(WIFI_CONNECT_TIMEOUT_MS);
  }

  float t = 0;
  float h = 0;
  if (!readDht(t, h)) {
    Serial.println("DHT11 read failed (перевір GPIO, pull-up, 3.3 V)");
    delay(5000);
    return;
  }

  postReading(t, h);
  delay(POST_INTERVAL_MS);
}
