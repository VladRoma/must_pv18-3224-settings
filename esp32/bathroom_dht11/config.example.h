#pragma once

// Скопіюй як config.h і підстав свої значення.

#define WIFI_SSID "Твій_WiFi_2.4GHz"
#define WIFI_PASSWORD "пароль_wifi"

// IP Raspberry Pi у локальній мережі (де крутиться must_settings.py --web --lan)
#define PI_HOST "192.168.1.100"
#define PI_PORT 8080

// Такий самий рядок у .env на Pi: BATHROOM_INGEST_TOKEN=...
#define INGEST_TOKEN "зміни_на_довгий_секрет"

// GPIO для DHT11 (ESP32-CAM без камери — часто GPIO 13 або 14)
#define DHT_PIN 13

// Інтервал між відправками, мс
#define POST_INTERVAL_MS 30000
