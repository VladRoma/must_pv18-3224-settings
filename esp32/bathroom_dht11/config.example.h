#pragma once

// Скопіюй як config.h і підстав свої значення.

#define WIFI_SSID "Твій_WiFi_2.4GHz"
#define WIFI_PASSWORD "пароль_wifi"

// IP Raspberry Pi (must_settings.py --web --lan), напр. 192.168.1.14
#define PI_HOST "192.168.1.100"
#define PI_PORT 8080

// Як BATHROOM_INGEST_TOKEN у .env / .must-bathroom-token на Pi
#define INGEST_TOKEN "зміни_на_довгий_секрет"

// DHT11 DATA (ESP32-CAM без камери: 13 або 14; потрібен pull-up 4.7–10 kΩ до 3.3 V)
#define DHT_PIN 13

#define POST_INTERVAL_MS 30000
#define HTTP_TIMEOUT_MS 8000
#define POST_RETRY_COUNT 3
#define POST_RETRY_DELAY_MS 2000
#define DHT_WARMUP_MS 2000
#define DHT_READ_RETRY 3
#define WIFI_CONNECT_TIMEOUT_MS 30000
