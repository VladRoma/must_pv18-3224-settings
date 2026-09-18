#!/usr/bin/env python3
"""Локальний веб-дашборд MUST PV18-3224 VPM II."""

from __future__ import annotations

import argparse
import hmac
import json
import mimetypes
import os
import secrets
import socket
import subprocess
import threading
import time
from datetime import datetime, timezone
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

import serial

import must_battery
import must_bathroom
import must_mongodb
import must_settings as core

WEB_DIR = Path(__file__).resolve().parent / "web"
PASSWORD_FILE = Path(__file__).resolve().parent / ".must-settings-password"
COOKIE_NAME = "must_auth"
SESSION_MAX_AGE = 12 * 3600
_read_lock = threading.Lock()


class SettingsAuth:
    """Пароль лише для вкладки «Налаштування» і запису в інвертор."""

    def __init__(self, password: str) -> None:
        self.password = password
        self._lock = threading.Lock()
        self._sessions: dict[str, float] = {}
        self._fails: dict[str, list[float]] = {}

    def login(self, password: str, ip: str) -> str | None:
        now = time.monotonic()
        with self._lock:
            recent = [stamp for stamp in self._fails.get(ip, []) if now - stamp < 60]
            self._fails[ip] = recent
            if len(recent) >= 8:
                return None
            if not hmac.compare_digest(password.encode("utf-8"), self.password.encode("utf-8")):
                recent.append(now)
                self._fails[ip] = recent
                return None
            self._fails.pop(ip, None)
            self._purge_locked(now)
            token = secrets.token_urlsafe(32)
            self._sessions[token] = now + SESSION_MAX_AGE
            return token

    def logout(self, token: str | None) -> None:
        if not token:
            return
        with self._lock:
            self._sessions.pop(token, None)

    def valid(self, token: str | None) -> bool:
        if not token:
            return False
        now = time.monotonic()
        with self._lock:
            self._purge_locked(now)
            expiry = self._sessions.get(token)
            if expiry is None:
                return False
            if expiry < now:
                self._sessions.pop(token, None)
                return False
            return True

    def _purge_locked(self, now: float) -> None:
        expired = [key for key, expiry in self._sessions.items() if expiry < now]
        for key in expired:
            del self._sessions[key]


def ensure_settings_password(args: argparse.Namespace) -> str:
    cli = str(getattr(args, "settings_password", "") or "").strip()
    if cli:
        return cli
    env = (os.environ.get("MUST_SETTINGS_PASSWORD") or "").strip()
    if env:
        return env
    if PASSWORD_FILE.is_file():
        lines = PASSWORD_FILE.read_text(encoding="utf-8").strip().splitlines()
        if lines and lines[0].strip():
            return lines[0].strip()
    password = secrets.token_urlsafe(8).replace("-", "").replace("_", "")[:10]
    PASSWORD_FILE.write_text(password + "\n", encoding="utf-8")
    try:
        PASSWORD_FILE.chmod(0o600)
    except OSError:
        pass
    print(f"Пароль вкладки «Налаштування»:  {password}")
    print(f"Файл: {PASSWORD_FILE}")
    return password


def _cookie_token(headers) -> str | None:
    raw = headers.get("Cookie", "") if headers else ""
    cookie = SimpleCookie()
    try:
        cookie.load(raw)
    except (TypeError, ValueError):
        return None
    morsel = cookie.get(COOKIE_NAME)
    return morsel.value if morsel else None


def _auth_cookie(token: str, max_age: int = SESSION_MAX_AGE) -> str:
    return f"{COOKIE_NAME}={token}; HttpOnly; Path=/; SameSite=Lax; Max-Age={max_age}"


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


def _empty_metric(label: str) -> dict:
    return {"label": label, "value": "—", "unit": ""}


def _metric_from(value: object, unit: str, label: str) -> dict:
    if value is None:
        return _empty_metric(label)
    return {"label": label, "value": value, "unit": unit}


def build_payload(args: argparse.Namespace) -> dict:
    sections: list[core.Section] = []
    writable: list[dict] = []
    inverter_error: str | None = None
    bms_only = bool(getattr(args, "bms_only", False))
    bms_host = getattr(args, "bms_host", None)
    data: dict[str, core.RegisterMap] | None = None

    if not bms_only:
        try:
            with _read_lock:
                data = core.probe_if_needed(args)
                sections = core.build_sections(data)
                writable = core.build_writable_settings(data)
        except (serial.SerialException, core.ModbusRtuError) as exc:
            inverter_error = str(exc)
            if not bms_host:
                raise

    def metric(label: str) -> dict:
        row = _find_row(sections, label)
        if not row:
            return _empty_metric(label)
        return {
            "label": label,
            "value": row["value"],
            "unit": row.get("unit") or "",
            "register": row.get("register"),
            "program": row.get("program"),
        }

    battery = None
    if bms_host:
        battery = must_battery.read_cached(
            bms_host,
            getattr(args, "bms_port", must_battery.DEFAULT_PORT),
            getattr(args, "timeout", must_battery.DEFAULT_TIMEOUT),
        )

    inverter_ok = inverter_error is None and not bms_only
    battery_ok = bool(battery and battery.get("ok"))
    if bms_only:
        inverter_ok = False
    elif sections:
        inverter_ok = True

    if not inverter_ok and not battery_ok:
        if inverter_error:
            raise core.ModbusRtuError(inverter_error)
        raise must_battery.BatteryConnectionError(
            battery.get("error") if battery else "немає даних АКБ"
        )

    metrics = {
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
    }
    numbers = {
        "soc": _parse_num(metrics["soc"]["value"]),
        "pv_w": _parse_num(metrics["pv_p"]["value"]),
        "load_w": _parse_num(metrics["load_p"]["value"]),
        "grid_w": _parse_num(metrics["grid_p"]["value"]),
        "batt_w": _parse_num(metrics["batt_p"]["value"]),
    }

    if battery_ok:
        metrics["soc"] = _metric_from(battery["soc"], "%", "BMS SOC")
        metrics["soh"] = _metric_from(battery["soh"], "%", "BMS SOH")
        metrics["bms_v"] = _metric_from(f"{battery['voltage']:.1f}", "В", "BMS напруга")
        metrics["bms_i"] = _metric_from(f"{battery['current']:.1f}", "А", "BMS струм")
        metrics["wifi_p"] = _metric_from(f"{battery['power']:.0f}", "Вт", "Потужність АКБ")
        metrics["wifi_state"] = _metric_from(battery["state"], "", "Стан АКБ")
        metrics["cycles"] = _metric_from(battery["cycles"], "", "Цикли")
        metrics["remain_ah"] = _metric_from(f"{battery['remaining_ah']:.1f}", "А·год", "Залишок")
        numbers["soc"] = battery["soc"]
        numbers["batt_w"] = battery["power"]
        if bms_only:
            metrics["state"] = metrics["wifi_state"]
            metrics["model"] = _metric_from("LP16-24200", "", "Модель")
            metrics["batt_p"] = metrics["wifi_p"]
            metrics["batt_v"] = metrics["bms_v"]
            metrics["batt_i"] = metrics["bms_i"]

    payload = {
        "ok": True,
        "updated": datetime.now().astimezone().isoformat(timespec="seconds"),
        "connection": {
            "port": args.port,
            "baud": args.baud,
            "slave": args.slave,
            "bms_host": bms_host,
            "bms_port": getattr(args, "bms_port", must_battery.DEFAULT_PORT),
            "bms_only": bms_only,
            "inverter_error": inverter_error,
        },
        "sections": sections,
        "writable": writable,
        "metrics": metrics,
        "numbers": numbers,
        "battery": battery,
    }
    must_mongodb.try_record(args, data, payload)
    return payload


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


def make_handler(args: argparse.Namespace, auth: SettingsAuth):
    class MustWebHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *log_args) -> None:
            print(f"[web] {self.address_string()} {fmt % log_args}")

        def _authed(self) -> bool:
            return auth.valid(_cookie_token(self.headers))

        def _send_bytes(
            self,
            code: int,
            body: bytes,
            content_type: str,
            extra_headers: list[tuple[str, str]] | None = None,
        ) -> None:
            self.send_response(code)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for name, value in extra_headers or []:
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(body)

        def _send_json(
            self,
            code: int,
            payload: dict,
            extra_headers: list[tuple[str, str]] | None = None,
        ) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self._send_bytes(code, body, "application/json; charset=utf-8", extra_headers)

        def _read_json(self) -> dict:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            return json.loads(raw.decode("utf-8"))

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

            if route == "/api/auth/status":
                self._send_json(200, {"ok": True, "authenticated": self._authed()})
                return

            if route == "/api/bathroom/data":
                self._send_json(200, must_bathroom.build_api_payload())
                return

            if route in ("/bathroom", "/bathroom/"):
                self._serve_file("bathroom/index.html")
                return

            if route == "/api/data":
                try:
                    payload = build_payload(args)
                    authed = self._authed()
                    payload["settings_auth"] = authed
                    if not authed:
                        payload["writable"] = []
                    self._send_json(200, payload)
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
                except must_battery.BatteryError as exc:
                    self._send_json(
                        503,
                        {"ok": False, "error": "battery", "message": str(exc)},
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
            route = parsed.path

            if route == "/api/bathroom/ingest":
                query = parsed.query or ""
                token_q = ""
                if "token=" in query:
                    for part in query.split("&"):
                        if part.startswith("token="):
                            token_q = part[6:]
                            break
                if not must_bathroom.ingest_token():
                    self._send_json(
                        503,
                        {
                            "ok": False,
                            "error": "config",
                            "message": "BATHROOM_INGEST_TOKEN не налаштовано на сервері",
                        },
                    )
                    return
                if not must_bathroom.check_ingest_auth(self.headers, token_q):
                    self._send_json(
                        401,
                        {"ok": False, "error": "auth", "message": "Невірний або відсутній X-Bathroom-Token"},
                    )
                    return
                try:
                    body = self._read_json()
                    payload = must_bathroom.ingest_reading(body, self.client_address[0])
                    self._send_json(200, payload)
                except ValueError as exc:
                    self._send_json(400, {"ok": False, "error": "value", "message": str(exc)})
                return

            if route == "/api/auth/login":
                try:
                    body = self._read_json()
                except ValueError:
                    self._send_json(400, {"ok": False, "error": "json", "message": "некоректний JSON"})
                    return
                password = str(body.get("password") or "")
                token = auth.login(password, self.client_address[0])
                if not token:
                    self._send_json(
                        401,
                        {"ok": False, "error": "auth", "message": "Невірний пароль"},
                    )
                    return
                self._send_json(
                    200,
                    {"ok": True, "authenticated": True},
                    extra_headers=[("Set-Cookie", _auth_cookie(token))],
                )
                return

            if route == "/api/auth/logout":
                auth.logout(_cookie_token(self.headers))
                self._send_json(
                    200,
                    {"ok": True, "authenticated": False},
                    extra_headers=[("Set-Cookie", _auth_cookie("deleted", max_age=0))],
                )
                return

            if route != "/api/settings/write":
                self._send_bytes(404, b"Not found", "text/plain; charset=utf-8")
                return

            if not self._authed():
                self._send_json(
                    401,
                    {"ok": False, "error": "auth", "message": "Спочатку введи пароль на вкладці Налаштування"},
                )
                return

            try:
                body = self._read_json()
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


def _guess_tailscale_ip() -> str | None:
    try:
        result = subprocess.run(
            ["tailscale", "ip", "-4"],
            check=True,
            capture_output=True,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    for line in result.stdout.splitlines():
        ip = line.strip()
        if ip.startswith("100."):
            return ip
    return None


def run_web(args: argparse.Namespace) -> None:
    password = ensure_settings_password(args)
    auth = SettingsAuth(password)
    must_bathroom.ensure_bathroom_ingest_token()
    host = _resolve_web_host(args)
    port = getattr(args, "http_port", 8080)
    handler = make_handler(args, auth)
    server = ThreadingHTTPServer((host, port), handler)
    print(f"MUST Web Dashboard  →  http://127.0.0.1:{port}/")
    if host == "0.0.0.0":
        lan_ip = _guess_lan_ip()
        if lan_ip:
            print(f"Телефон у Wi‑Fi       →  http://{lan_ip}:{port}/")
        else:
            print(f"Локальна мережа       →  http://<IP-Pi>:{port}/")
        ts_ip = _guess_tailscale_ip()
        if ts_ip:
            print(f"Tailscale             →  http://{ts_ip}:{port}/")
    print("Вкладка «Налаштування» захищена паролем.")
    print(f"Ванна (DHT11)         →  http://127.0.0.1:{port}/bathroom/")
    if host == "0.0.0.0":
        lan_ip = _guess_lan_ip()
        if lan_ip:
            print(f"Ванна у Wi‑Fi         →  http://{lan_ip}:{port}/bathroom/")
    if getattr(args, "bms_only", False):
        print(
            f"АКБ Wi‑Fi {getattr(args, 'bms_host', '—')}:"
            f"{getattr(args, 'bms_port', must_battery.DEFAULT_PORT)}"
        )
    else:
        print(f"Serial {args.port}  ·  {args.baud} 8N1  ·  slave {args.slave}")
        bms_host = getattr(args, "bms_host", None)
        if bms_host:
            print(
                f"АКБ Wi‑Fi {bms_host}:"
                f"{getattr(args, 'bms_port', must_battery.DEFAULT_PORT)}"
            )
    mongo_line = must_mongodb.status_line(args)
    if mongo_line:
        print(mongo_line)
    print("Ctrl+C — зупинити сервер")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nЗупинено.")
    finally:
        server.server_close()


if __name__ == "__main__":
    run_web(core.parse_args())
