#!/usr/bin/env python3
"""Зведений домашній дашборд: інвертор, АКБ, ванна, провітрювання."""

from __future__ import annotations

import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import must_bathroom

_EXT5V_PATTERNS = (
    re.compile(r"EXT5V_V\s+volt\(\d+\)=([0-9.]+)\s*V", re.IGNORECASE),
    re.compile(r"EXT5V_V\s+volt\(([0-9.]+)\s*V\)", re.IGNORECASE),
)


def _parse_ext5v_volts(raw: str) -> float | None:
    for pattern in _EXT5V_PATTERNS:
        match = pattern.search(raw)
        if match:
            return float(match.group(1))
    return None


def _ext5v_hint(volts: float) -> str:
    if volts < 4.75:
        return "Низька напруга — перевір блок живлення або кабель USB‑C."
    if volts > 5.25:
        return "Підвищена напруга на вході."
    return "Норма для живлення Raspberry Pi (шина 5 V)."


def read_pi_ext5v() -> dict[str, Any]:
    """Напруга EXT5V на Raspberry Pi (Pi 5 / pmic), через vcgencmd."""
    try:
        result = subprocess.run(
            ["vcgencmd", "pmic_read_adc"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {
            "ok": False,
            "available": False,
            "error": str(exc),
            "raw": None,
            "volts": None,
            "label": "Вхідна напруга",
            "display": "—",
            "hint": None,
        }

    if result.returncode != 0:
        err = (result.stderr or result.stdout or "vcgencmd failed").strip()
        return {
            "ok": False,
            "available": True,
            "error": err,
            "raw": None,
            "volts": None,
            "label": "Вхідна напруга",
            "display": "—",
            "hint": None,
        }

    for line in result.stdout.splitlines():
        if "EXT5V_V" not in line:
            continue
        raw = line.strip()
        parsed = _parse_ext5v_volts(raw)
        if parsed is None:
            return {
                "ok": False,
                "available": True,
                "error": "не вдалося розібрати EXT5V_V",
                "raw": raw,
                "volts": None,
                "label": "Вхідна напруга",
                "display": "—",
                "hint": None,
            }
        volts = round(parsed, 3)
        return {
            "ok": True,
            "available": True,
            "raw": raw,
            "volts": volts,
            "label": "Вхідна напруга",
            "display": f"{volts:.2f} V",
            "hint": _ext5v_hint(volts),
        }

    return {
        "ok": False,
        "available": True,
        "error": "EXT5V_V не знайдено у pmic_read_adc",
        "raw": None,
        "volts": None,
        "label": "Вхідна напруга",
        "display": "—",
        "hint": None,
    }


_TEMP_VCGENCMD_RE = re.compile(r"temp=([0-9.]+)'C", re.IGNORECASE)
_THERMAL_ZONE = Path("/sys/class/thermal/thermal_zone0/temp")


def _temp_hint(celsius: float) -> str:
    if celsius >= 80:
        return "Дуже гаряче — перевір охолодження або навантаження."
    if celsius >= 70:
        return "Тепло — можливе throttling під навантаженням."
    return "Нормальна температура для Raspberry Pi 5."


def read_pi_temperature() -> dict[str, Any]:
    """Температура CPU Raspberry Pi (vcgencmd або thermal_zone0)."""
    base = {
        "label": "Температура CPU",
        "display": "—",
        "hint": None,
        "celsius": None,
        "raw": None,
    }

    try:
        result = subprocess.run(
            ["vcgencmd", "measure_temp"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
        if result.returncode == 0:
            raw = result.stdout.strip()
            match = _TEMP_VCGENCMD_RE.search(raw)
            if match:
                celsius = round(float(match.group(1)), 1)
                return {
                    "ok": True,
                    "available": True,
                    "raw": raw,
                    "celsius": celsius,
                    "label": base["label"],
                    "display": f"{celsius:.1f} °C",
                    "hint": _temp_hint(celsius),
                    "error": None,
                }
    except (OSError, subprocess.SubprocessError):
        pass

    try:
        if _THERMAL_ZONE.is_file():
            millideg = int(_THERMAL_ZONE.read_text(encoding="utf-8").strip())
            celsius = round(millideg / 1000.0, 1)
            return {
                "ok": True,
                "available": True,
                "raw": f"thermal_zone0={millideg}",
                "celsius": celsius,
                "label": base["label"],
                "display": f"{celsius:.1f} °C",
                "hint": _temp_hint(celsius),
                "error": None,
            }
    except (OSError, ValueError):
        pass

    return {
        "ok": False,
        "available": False,
        "error": "немає vcgencmd measure_temp або thermal_zone0",
        **base,
    }


def read_pi_status() -> dict[str, Any]:
    return {
        "power": read_pi_ext5v(),
        "temperature": read_pi_temperature(),
    }


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
        "pi": read_pi_status(),
        "pi_power": read_pi_ext5v(),
    }
