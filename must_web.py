#!/usr/bin/env python3
"""Локальний веб-дашборд MUST PV18-3224 VPM II."""

from __future__ import annotations

import argparse
import json
import mimetypes
import socket
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import serial

import must_settings as core

WEB_DIR = Path(__file__).resolve().parent / "web"
_read_lock = threading.Lock()


def _find_row(sections: list[core.Section], label: str) -> core.Row | None:
    for section in sections:
        for row in section["rows"]:
            if row["label"] == label:
                return row
    return None


def _parse_num(text: object) -> float | None:
    if text is None or text == "—":
        return None
    import re

    match = re.search(r"-?\d+(?:[.,]\d+)?", str(text))
    if not match:
        return None
    return float(match.group(0).replace(",", "."))


def build_payload(args: argparse.Namespace) -> dict:
    with _read_lock:
        data = core.probe_if_needed(args)
        sections = core.build_sections(data)
        writable = core.build_writable_settings(data)

    def metric(label: str) -> dict:
        row = _find_row(sections, label)
        if not row:
            return {"label": label, "value": "—", "unit": ""}
        return {
            "label": label,
            "value": row["value"],
            "unit": row.get("unit") or "",
            "register": row.get("register"),
            "program": row.get("program"),
        }

    return {
        "ok": True,
        "updated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "connection": {
            "port": args.port,
            "baud": args.baud,
            "slave": args.slave,
        },
        "sections": sections,
        "writable": writable,
        "metrics": {
            "soc": metric("BMS SOC"),
            "soh": metric("BMS SOH"),
            "state": metric("Робочий стан"),
            "model": metric("Модель"),
            "firmware": metric("Прошивка"),
            "nominal_power": metric("Номінальна потужність"),
            "protocol": metric("Modbus-протокол"),
            "pv_p": metric("PV потужність"),
            "load_p": metric("Потужність навантаження"),
            "grid_p": metric("Потужність мережі"),
            "batt_p": metric("Потужність АКБ (інвертор)"),
            "batt_v": metric("Напруга АКБ (інвертор)"),
            "batt_i": metric("Струм АКБ (інвертор)"),
            "bms_v": metric("BMS напруга"),
            "bms_i": metric("BMS струм"),
            "bms_t": metric("BMS температура"),
            "bms_err": metric("BMS помилки"),
            "bat_type": metric("Тип акумулятора"),
        },
        "numbers": {
            "soc": _parse_num(metric("BMS SOC")["value"]),
            "pv_w": _parse_num(metric("PV потужність")["value"]),
            "load_w": _parse_num(metric("Потужність навантаження")["value"]),
            "grid_w": _parse_num(metric("Потужність мережі")["value"]),
            "batt_w": _parse_num(metric("Потужність АКБ (інвертор)")["value"]),
        },
    }


def write_setting_payload(args: argparse.Namespace, body: dict) -> dict:
    program = body.get("program")
    value = body.get("value")
    if program is None or value is None:
        raise ValueError("потрібні поля program та value")

    with _read_lock:
        client = core.ModbusRtuClient(args.port, args.baud, args.slave, args.timeout)
        try:
            data = core.read_all(client)
            result = core.apply_setting_write(client, data, int(program), value)
        finally:
            client.close()

    return {
        "ok": True,
        "updated": datetime.now().astimezone().isoformat(timespec="seconds"),
        **result,
    }


def make_handler(args: argparse.Namespace):
    class MustWebHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *log_args) -> None:
            print(f"[web] {self.address_string()} {fmt % log_args}")

        def _send_bytes(self, code: int, body: bytes, content_type: str) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, code: int, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send_bytes(code, body, "application/json; charset=utf-8")

        def _serve_file(self, rel_path: str) -> None:
            path = (WEB_DIR / rel_path).resolve()
            if not str(path).startswith(str(WEB_DIR.resolve())) or not path.is_file():
                self._send_bytes(404, b"Not found", "text/plain; charset=utf-8")
                return
            mime, _ = mimetypes.guess_type(path.name)
            body = path.read_bytes()
            self._send_bytes(200, body, mime or "application/octet-stream")

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            route = parsed.path

            if route == "/api/data":
                try:
                    self._send_json(200, build_payload(args))
                except serial.SerialException as exc:
                    self._send_json(
                        503,
                        {
                            "ok": False,
                            "error": "serial",
                            "message": f"Не вдалося відкрити {args.port}: {exc}",
                        },
                    )
                except core.ModbusRtuError as exc:
                    self._send_json(
                        503,
                        {"ok": False, "error": "modbus", "message": str(exc)},
                    )
                return

            if route in ("/", "/index.html"):
                self._serve_file("index.html")
                return

            if route.startswith("/"):
                candidate = route.lstrip("/")
                if candidate and (WEB_DIR / candidate).is_file():
                    self._serve_file(candidate)
                    return

            self._send_bytes(404, b"Not found", "text/plain; charset=utf-8")

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path != "/api/settings/write":
                self._send_bytes(404, b"Not found", "text/plain; charset=utf-8")
                return

            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            try:
                body = json.loads(raw.decode("utf-8"))
                payload = write_setting_payload(args, body)
                self._send_json(200, payload)
            except ValueError as exc:
                self._send_json(400, {"ok": False, "error": "value", "message": str(exc)})
            except serial.SerialException as exc:
                self._send_json(
                    503,
                    {"ok": False, "error": "serial", "message": f"Не вдалося відкрити {args.port}: {exc}"},
                )
            except core.ModbusRtuError as exc:
                self._send_json(503, {"ok": False, "error": "modbus", "message": str(exc)})

    return MustWebHandler


def _resolve_web_host(args: argparse.Namespace) -> str:
    if getattr(args, "lan", False):
        return "0.0.0.0"
    return getattr(args, "host", "127.0.0.1")


def _guess_lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return None


def run_web(args: argparse.Namespace) -> None:
    host = _resolve_web_host(args)
    port = getattr(args, "http_port", 8080)
    handler = make_handler(args)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"MUST Web Dashboard  →  http://127.0.0.1:{port}/")
    if host == "0.0.0.0":
        lan_ip = _guess_lan_ip()
        if lan_ip:
            print(f"Телефон у Wi‑Fi       →  http://{lan_ip}:{port}/")
        else:
            print(f"Локальна мережа       →  http://<IP-Pi>:{port}/")
    print(f"Serial {args.port}  ·  {args.baud} 8N1  ·  slave {args.slave}")
    print("Ctrl+C — зупинити сервер")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nЗупинено.")
    finally:
        server.server_close()


if __name__ == "__main__":
    run_web(core.parse_args())
