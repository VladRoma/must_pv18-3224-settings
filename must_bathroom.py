#!/usr/bin/env python3
"""Телеметрія ванної (ESP32 + DHT11) для веб-дашборду на Raspberry Pi."""

from __future__ import annotations

import math
import os
import secrets
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_ENV_LOADED = False
_lock = threading.Lock()
_latest: dict[str, Any] | None = None
_history: list[dict[str, Any]] = []

DEFAULT_STALE_SEC = 120
MAX_HISTORY = 576  # ~48 год при інтервалі 5 хв
TOKEN_FILE = Path(__file__).resolve().parent / ".must-bathroom-token"


def load_env() -> None:
    global _ENV_LOADED
    if _ENV_LOADED:
        return
    env_path = Path(__file__).resolve().parent / ".env"
    if env_path.is_file():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            text = line.strip()
            if not text or text.startswith("#") or "=" not in text:
                continue
            key, value = text.split("=", 1)
            key = key.strip()
            value = value.strip().strip('"').strip("'")
            if key and key not in os.environ:
                os.environ[key] = value
    _ENV_LOADED = True


def ingest_token() -> str:
    load_env()
    env = (os.environ.get("BATHROOM_INGEST_TOKEN") or "").strip()
    if env:
        return env
    if TOKEN_FILE.is_file():
        lines = TOKEN_FILE.read_text(encoding="utf-8").strip().splitlines()
        if lines and lines[0].strip():
            return lines[0].strip()
    return ""


def ensure_bathroom_ingest_token() -> str:
    """Обов'язковий токен для POST /api/bathroom/ingest (env, .env або автогенерація)."""
    token = ingest_token()
    if token:
        return token
    token = secrets.token_urlsafe(16).replace("-", "").replace("_", "")[:16]
    TOKEN_FILE.write_text(token + "\n", encoding="utf-8")
    try:
        TOKEN_FILE.chmod(0o600)
    except OSError:
        pass
    os.environ["BATHROOM_INGEST_TOKEN"] = token
    print(f"Токен ESP32 (ванна):     {token}")
    print(f"Скопіюй у config.h → INGEST_TOKEN, або задай BATHROOM_INGEST_TOKEN у .env")
    print(f"Файл: {TOKEN_FILE}")
    return token


def stale_seconds() -> int:
    load_env()
    raw = (os.environ.get("BATHROOM_STALE_SEC") or "").strip()
    if not raw:
        return DEFAULT_STALE_SEC
    try:
        return max(30, int(raw))
    except ValueError:
        return DEFAULT_STALE_SEC


def dew_point_c(temp_c: float, humidity_pct: float) -> float:
    """Точка роси (Magnus, °C)."""
    rh = max(0.1, min(100.0, humidity_pct))
    t = temp_c
    a, b = 17.62, 243.12
    gamma = math.log(rh / 100.0) + (a * t) / (b + t)
    return (b * gamma) / (a - gamma)


def comfort_label(temp_c: float, humidity_pct: float) -> tuple[str, str, str]:
    """Повертає (код, заголовок, підказка)."""
    if humidity_pct >= 80:
        return (
            "critical",
            "Дуже волого",
            "Увімкни витяжку або провітри — ризик конденсату та цвілі.",
        )
    if humidity_pct >= 70:
        return (
            "warn",
            "Підвищена вологість",
            "Після душу варто провітрити або увімкнути вентиляцію.",
        )
    if humidity_pct < 30:
        return ("dry", "Сухо", "Можливий дискомфорт для шкіри та дихання.")
    if temp_c < 18:
        return ("cold", "Прохолодно", "Комфортна ванна зазвичай від ~20 °C.")
    if temp_c > 28:
        return ("hot", "Спекотно", "Висока температура підсилює відчуття вологості.")
    return ("ok", "Комфортно", "Температура та вологість у нормальному діапазоні.")


def ingest_reading(body: dict[str, Any], client_ip: str) -> dict[str, Any]:
    try:
        temp = float(body["temperature_c"])
        hum = float(body["humidity_pct"])
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Потрібні числа temperature_c та humidity_pct") from exc

    if not (-40 <= temp <= 85):
        raise ValueError("temperature_c поза діапазоном DHT11")
    if not (0 <= hum <= 100):
        raise ValueError("humidity_pct має бути 0–100")

    device = str(body.get("device") or "esp32-bathroom").strip()[:64]
    now = datetime.now(timezone.utc)
    stamp = now.isoformat(timespec="seconds")
    dew = round(dew_point_c(temp, hum), 1)
    code, title, hint = comfort_label(temp, hum)

    row = {
        "at": stamp,
        "temperature_c": round(temp, 1),
        "humidity_pct": round(hum, 1),
        "dew_point_c": dew,
        "comfort_code": code,
        "comfort_title": title,
        "device": device,
        "source_ip": client_ip,
    }

    global _latest, _history
    with _lock:
        _latest = row
        _history.append(
            {
                "at": stamp,
                "temperature_c": row["temperature_c"],
                "humidity_pct": row["humidity_pct"],
            }
        )
        if len(_history) > MAX_HISTORY:
            _history = _history[-MAX_HISTORY:]

    return {"ok": True, "stored": row}


def build_api_payload() -> dict[str, Any]:
    stale = stale_seconds()
    now_mono = time.time()

    with _lock:
        latest = dict(_latest) if _latest else None
        history = list(_history)

    online = False
    age_sec: float | None = None
    if latest and latest.get("at"):
        try:
            ts = datetime.fromisoformat(latest["at"].replace("Z", "+00:00"))
            age_sec = max(0.0, (datetime.now(timezone.utc) - ts).total_seconds())
            online = age_sec <= stale
        except ValueError:
            online = False

    comfort = None
    if latest:
        code, title, hint = comfort_label(
            latest["temperature_c"], latest["humidity_pct"]
        )
        comfort = {"code": code, "title": title, "hint": hint}

    return {
        "ok": True,
        "online": online,
        "stale_after_sec": stale,
        "age_sec": round(age_sec, 1) if age_sec is not None else None,
        "updated": latest["at"] if latest else None,
        "reading": latest,
        "comfort": comfort,
        "history": history[-120:],
    }


def check_ingest_auth(headers: Any, token_arg: str | None) -> bool:
    expected = ingest_token()
    if not expected:
        return False
    got = (token_arg or "").strip()
    if not got:
        got = (headers.get("X-Bathroom-Token") or headers.get("x-bathroom-token") or "").strip()
    return got == expected
