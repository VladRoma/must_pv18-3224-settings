#!/usr/bin/env python3
"""Запис телеметрії MUST PV18 у MongoDB Atlas."""

from __future__ import annotations

import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote_plus

import must_settings as core

_ENV_LOADED = False

DEFAULT_DB = "must_pv18"
DEFAULT_CLUSTER_URI = "mongodb+srv://userHome:{password}@cluster0.ehywaos.mongodb.net/"

_client: Any = None
_db: Any = None
_indexes_ready = False


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


def mongo_enabled(args: Any) -> bool:
    load_env()
    if getattr(args, "mongo", False):
        return True
    if (os.environ.get("MONGODB_URI") or "").strip():
        return True
    if (os.environ.get("MONGODB_PASSWORD") or "").strip():
        return True
    return False


def resolve_uri(args: Any) -> str | None:
    uri = (getattr(args, "mongo_uri", None) or os.environ.get("MONGODB_URI") or "").strip()
    password = (os.environ.get("MONGODB_PASSWORD") or "").strip()

    if uri:
        if "<db_password>" in uri:
            if not password:
                return None
            uri = uri.replace("<db_password>", quote_plus(password))
        return uri

    if password:
        return DEFAULT_CLUSTER_URI.format(password=quote_plus(password))

    return None


def resolve_db_name(args: Any) -> str:
    return (
        (getattr(args, "mongo_db", None) or os.environ.get("MONGODB_DB") or DEFAULT_DB).strip()
        or DEFAULT_DB
    )


def _get_db(args: Any):
    global _client, _db, _indexes_ready
    uri = resolve_uri(args)
    if not uri:
        raise RuntimeError("MongoDB: задай MONGODB_URI або MONGODB_PASSWORD")

    if _db is not None:
        return _db

    try:
        from pymongo import MongoClient
    except ImportError as exc:
        raise RuntimeError("встанови pymongo: pip install pymongo[srv]") from exc

    _client = MongoClient(uri, serverSelectionTimeoutMS=8000)
    _client.admin.command("ping")
    _db = _client[resolve_db_name(args)]

    if not _indexes_ready:
        _db["telemetry"].create_index([("ts", -1)])
        _db["telemetry"].create_index([("errors.bms_code", 1), ("ts", -1)])
        _db["bms_errors"].create_index([("ts", -1)])
        _indexes_ready = True

    return _db


def _row_map(sections: list[core.Section]) -> dict[str, core.Row]:
    rows: dict[str, core.Row] = {}
    for section in sections:
        for row in section["rows"]:
            rows[str(row["label"])] = row
    return rows


def _num(value: object) -> float | None:
    if value is None or value == "—":
        return None
    try:
        return float(str(value).replace(",", ".").split()[0])
    except (TypeError, ValueError):
        return None


def build_document(
    args: Any,
    data: dict[str, core.RegisterMap] | None,
    payload: dict[str, Any],
) -> dict[str, Any]:
    sections = payload.get("sections") or []
    rows = _row_map(sections)
    metrics = payload.get("metrics") or {}
    numbers = payload.get("numbers") or {}
    battery_wifi = payload.get("battery")
    conn = payload.get("connection") or {}

    bms = data.get("bms", {}) if data else {}
    inv = data.get("inverter", {}) if data else {}
    live = data.get("live", {}) if data else {}
    charger = data.get("charger", {}) if data else {}

    bms_code = int(bms.get(112, 0)) if bms else 0
    bms_err_text = str(rows.get("BMS помилки", {}).get("value", "—"))

    doc: dict[str, Any] = {
        "ts": datetime.now(timezone.utc),
        "updated_local": payload.get("updated"),
        "connection": {
            "port": conn.get("port"),
            "baud": conn.get("baud"),
            "slave": conn.get("slave"),
            "bms_host": conn.get("bms_host"),
            "bms_port": conn.get("bms_port"),
        },
        "battery": {},
        "inverter": {},
        "charging": {},
        "errors": {
            "bms_code": bms_code,
            "bms_text": bms_err_text,
        },
    }

    if bms or rows.get("BMS SOC"):
        doc["battery"] = {
            "source": "modbus_bms",
            "soc": _num(rows.get("BMS SOC", {}).get("value")),
            "soh": _num(rows.get("BMS SOH", {}).get("value")),
            "voltage_v": _num(rows.get("BMS напруга", {}).get("value")),
            "current_a": _num(rows.get("BMS струм", {}).get("value")),
            "temperature_c": _num(rows.get("BMS температура", {}).get("value")),
            "error_code": bms_code,
            "error_text": bms_err_text,
        }

    if battery_wifi and battery_wifi.get("ok"):
        doc["battery"] = {
            "source": "wifi_paceex",
            "host": battery_wifi.get("host"),
            "serial": battery_wifi.get("serial"),
            "soc": battery_wifi.get("soc"),
            "soh": battery_wifi.get("soh"),
            "voltage_v": battery_wifi.get("voltage"),
            "current_a": battery_wifi.get("current"),
            "power_w": battery_wifi.get("power"),
            "remaining_ah": battery_wifi.get("remaining_ah"),
            "design_ah": battery_wifi.get("design_ah"),
            "cycles": battery_wifi.get("cycles"),
            "state": battery_wifi.get("state"),
            "cell_count": battery_wifi.get("cell_count"),
            "cell_min": battery_wifi.get("cell_min"),
            "cell_max": battery_wifi.get("cell_max"),
            "cell_delta": battery_wifi.get("cell_delta"),
            "cells": battery_wifi.get("cells"),
            "error_code": 0,
            "error_text": battery_wifi.get("error") or "немає",
        }

    if data or sections:
        doc["inverter"] = {
            "model": rows.get("Модель", {}).get("value"),
            "firmware": rows.get("Прошивка", {}).get("value"),
            "state": rows.get("Робочий стан", {}).get("value"),
            "work_state_code": live.get(25201) if live else None,
            "batt_voltage_v": _num(rows.get("Напруга АКБ (інвертор)", {}).get("value")),
            "batt_current_a": _num(rows.get("Струм АКБ (інвертор)", {}).get("value")),
            "batt_power_w": numbers.get("batt_w"),
            "pv_power_w": numbers.get("pv_w"),
            "load_power_w": numbers.get("load_w"),
            "grid_power_w": numbers.get("grid_w"),
            "grid_voltage_v": _num(rows.get("Напруга мережі", {}).get("value")),
            "load_percent": _num(rows.get("Завантаження", {}).get("value")),
            "temp_ac_c": _num(rows.get("Темп. AC радіатора", {}).get("value")),
            "temp_dc_c": _num(rows.get("Темп. DC радіатора", {}).get("value")),
        }

        doc["charging"] = {
            "charger_source": rows.get("Пріоритет джерела заряджання", {}).get("value"),
            "solar_priority": rows.get("Пріоритет сонячної енергії", {}).get("value"),
            "max_charge_a": _num(rows.get("Макс. струм заряджання", {}).get("value")),
            "grid_charge_a": _num(rows.get("Струм заряджання від мережі", {}).get("value")),
            "battery_type": rows.get("Тип акумулятора", {}).get("value"),
            "bulk_v": _num(rows.get("Напруга насичення (Bulk / C.V.)", {}).get("value")),
            "float_v": _num(rows.get("Напруга буферного заряду (Float)", {}).get("value")),
            "low_cutoff": rows.get(
                "Відсікання низької SOC" if "Відсікання низької SOC" in rows else "Відсікання низької DC-напруги",
                {},
            ).get("value"),
            "stop_discharge_v": _num(rows.get("Стоп розряду АКБ (з мережею)", {}).get("value")),
            "stop_charge_v": _num(rows.get("Стоп заряду АКБ", {}).get("value")),
            "bms_method": rows.get("Метод контролю BMS", {}).get("value"),
            "soc_stop_discharge": _num(rows.get("SOC — стоп розряду", {}).get("value")),
            "soc_stop_charge": _num(rows.get("SOC — стоп заряду", {}).get("value")),
            "raw_registers": {
                "20132_max_charge": inv.get(20132),
                "20125_grid_charge": inv.get(20125),
                "20123_bulk": inv.get(20123),
                "20122_float": inv.get(20122),
                "10110_bat_type": charger.get(10110) if charger else None,
                "25273_batt_p": live.get(25273) if live else None,
                "25274_batt_i": live.get(25274) if live else None,
                "25205_batt_v": live.get(25205) if live else None,
            },
        }

    return doc


def record_reading(
    args: Any,
    data: dict[str, core.RegisterMap] | None,
    payload: dict[str, Any],
) -> str | None:
    if not mongo_enabled(args):
        return None

    db = _get_db(args)
    doc = build_document(args, data, payload)
    result = db["telemetry"].insert_one(doc)

    bms_code = doc.get("errors", {}).get("bms_code") or 0
    if bms_code:
        err_doc = {
            "ts": doc["ts"],
            "telemetry_id": result.inserted_id,
            "bms_code": bms_code,
            "bms_text": doc.get("errors", {}).get("bms_text"),
            "battery": doc.get("battery"),
            "inverter_state": (doc.get("inverter") or {}).get("state"),
        }
        db["bms_errors"].insert_one(err_doc)

    return str(result.inserted_id)


def try_record(
    args: Any,
    data: dict[str, core.RegisterMap] | None,
    payload: dict[str, Any],
) -> None:
    if not mongo_enabled(args):
        return
    try:
        inserted = record_reading(args, data, payload)
        if inserted and getattr(args, "mongo_verbose", False):
            print(f"[mongo] записано telemetry {inserted}")
    except Exception as exc:
        print(f"[mongo] помилка запису: {exc}", file=sys.stderr)


def status_line(args: Any) -> str | None:
    if not mongo_enabled(args):
        return None
    uri = resolve_uri(args) or ""
    host = "cluster0.ehywaos.mongodb.net" if "cluster0.ehywaos" in uri else "MongoDB Atlas"
    return f"MongoDB → {resolve_db_name(args)} @ {host}"
