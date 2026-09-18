#!/usr/bin/env python3
"""Зведений домашній дашборд: інвертор, АКБ, ванна, провітрювання."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import must_bathroom


def ventilation_advice(bathroom: dict[str, Any]) -> dict[str, Any]:
    """Рішення щодо провітрювання за вологістю та точкою роси."""
    if not bathroom.get("online"):
        return {
            "code": "offline",
            "title": "Немає свіжих даних ванни",
            "detail": "Перевір ESP32 і Wi‑Fi.",
            "ventilate": None,
        }

    reading = bathroom.get("reading")
    if not reading:
        return {
            "code": "offline",
            "title": "Ванна ще не надсилала показники",
            "detail": "Зачекай на перший POST від ESP32.",
            "ventilate": None,
        }

    hum = float(reading["humidity_pct"])
    temp = float(reading["temperature_c"])
    dew = reading.get("dew_point_c")
    spread = (temp - float(dew)) if dew is not None else None

    if hum >= 80:
        return {
            "code": "now",
            "title": "Провітрити зараз",
            "detail": f"Вологість {hum:.0f}% — увімкни витяжку на 15–30 хв або провітри.",
            "ventilate": True,
            "humidity_pct": hum,
            "spread_c": spread,
        }
    if hum >= 70:
        return {
            "code": "soon",
            "title": "Рекомендовано провітрити",
            "detail": f"Вологість {hum:.0f}% — після душу варто витяжка або коротке провітрювання.",
            "ventilate": True,
            "humidity_pct": hum,
            "spread_c": spread,
        }
    if hum >= 65:
        return {
            "code": "watch",
            "title": "Стеж за вологістю",
            "detail": f"{hum:.0f}% — поки нормально, але близько до межі комфорту.",
            "ventilate": False,
            "humidity_pct": hum,
            "spread_c": spread,
        }
    if spread is not None and spread < 3:
        return {
            "code": "soon",
            "title": "Ризик конденсату",
            "detail": f"Точка роси близько до температури (Δ {spread:.1f} °C) — коротке провітрювання.",
            "ventilate": True,
            "humidity_pct": hum,
            "spread_c": spread,
        }
    return {
        "code": "ok",
        "title": "Провітрювання не потрібне",
        "detail": f"Вологість {hum:.0f}% у комфортному діапазоні.",
        "ventilate": False,
        "humidity_pct": hum,
        "spread_c": spread,
    }


def _metric_value(metrics: dict, key: str) -> str:
    row = metrics.get(key) or {}
    val = row.get("value", "—")
    unit = row.get("unit") or ""
    if val == "—" or not unit:
        return str(val)
    return f"{val} {unit}".strip()


def slim_must(must_payload: dict[str, Any]) -> dict[str, Any]:
    metrics = must_payload.get("metrics") or {}
    battery = must_payload.get("battery")
    bat_slim = None
    if battery and battery.get("ok"):
        bat_slim = {
            "soc": battery.get("soc"),
            "power": battery.get("power"),
            "state": battery.get("state"),
            "voltage": battery.get("voltage"),
            "current": battery.get("current"),
        }
    return {
        "ok": True,
        "updated": must_payload.get("updated"),
        "inverter_error": (must_payload.get("connection") or {}).get("inverter_error"),
        "soc": _metric_value(metrics, "soc"),
        "state": _metric_value(metrics, "state"),
        "pv": _metric_value(metrics, "pv_p"),
        "load": _metric_value(metrics, "load_p"),
        "batt": _metric_value(metrics, "batt_p"),
        "bms_err": _metric_value(metrics, "bms_err"),
        "numbers": must_payload.get("numbers") or {},
        "battery": bat_slim,
    }


def slim_bathroom(bathroom: dict[str, Any]) -> dict[str, Any]:
    r = bathroom.get("reading")
    if not r:
        return {
            "online": bathroom.get("online", False),
            "updated": bathroom.get("updated"),
            "temperature_c": None,
            "humidity_pct": None,
            "dew_point_c": None,
        }
    return {
        "online": bathroom.get("online", False),
        "updated": bathroom.get("updated"),
        "temperature_c": r.get("temperature_c"),
        "humidity_pct": r.get("humidity_pct"),
        "dew_point_c": r.get("dew_point_c"),
        "comfort_title": (bathroom.get("comfort") or {}).get("title"),
    }


def build_summary(must_payload: dict[str, Any] | None, must_error: str | None) -> dict[str, Any]:
    bathroom = must_bathroom.build_api_payload()
    vent = ventilation_advice(bathroom)

    must_block: dict[str, Any]
    if must_payload:
        must_block = slim_must(must_payload)
    else:
        must_block = {
            "ok": False,
            "error": must_error or "немає даних інвертора",
            "soc": "—",
            "state": "—",
            "pv": "—",
            "load": "—",
            "batt": "—",
            "bms_err": "—",
            "battery": None,
        }

    return {
        "ok": True,
        "updated": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "ventilation": vent,
        "must": must_block,
        "bathroom": slim_bathroom(bathroom),
    }
