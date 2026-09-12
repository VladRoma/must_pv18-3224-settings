#!/usr/bin/env python3
"""Локальне Wi‑Fi підключення до MUST LP16-24200 (PACEEX / PeiCheng BMS).

Модуль батареї після роздачі мережі в додатку BMS-Tool / Paceex BMS слухає
TCP-порт 8888 у локальній мережі (кадри 0x9A … CRC … 0x9D). Хмара не потрібна.
"""

from __future__ import annotations

import argparse
import json
import socket
import sys
import threading
import time
import webbrowser
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Any

DEFAULT_PORT = 8888
DEFAULT_TIMEOUT = 3.0
QUERY_COOLDOWN = 1.0
QUERY_RETRY_DELAYS = (0.4, 1.0)
SCAN_TIMEOUT = 0.35
SCAN_WORKERS = 64
CACHE_TTL = 8.0

STATUS_QUERY = bytes.fromhex("9a00000a0000000019519d")
CELLS_QUERY = bytes.fromhex("9a00000a020000020101289c9d")
SERIAL_QUERY = bytes.fromhex("9a00000002000000a0c89d")

_cache_lock = threading.Lock()
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


class BatteryError(RuntimeError):
    pass


class BatteryConnectionError(BatteryError):
    pass


class BatteryProtocolError(BatteryError):
    pass


def crc16(data: bytes) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= byte
        for _ in range(8):
            if crc & 1:
                crc = (crc >> 1) ^ 0xA001
            else:
                crc >>= 1
    return crc


def charge_state(current_a: float) -> str:
    if current_a > 0.2:
        return "Заряд"
    if current_a < -0.2:
        return "Розряд"
    return "Спокій"


@dataclass(frozen=True)
class BatteryReading:
    host: str
    port: int
    serial: str | None
    soc: int
    soh: int
    voltage: float
    current: float
    power: float
    remaining_ah: float
    design_ah: float
    cycles: int
    cell_count: int
    cells: list[float]
    cell_min: float
    cell_max: float
    cell_delta: float

    @property
    def state(self) -> str:
        return charge_state(self.current)

    def as_dict(self) -> dict[str, Any]:
        return {
            "ok": True,
            "host": self.host,
            "port": self.port,
            "serial": self.serial,
            "soc": self.soc,
            "soh": self.soh,
            "voltage": self.voltage,
            "current": self.current,
            "power": self.power,
            "remaining_ah": self.remaining_ah,
            "design_ah": self.design_ah,
            "cycles": self.cycles,
            "cell_count": self.cell_count,
            "cells": self.cells,
            "cell_min": self.cell_min,
            "cell_max": self.cell_max,
            "cell_delta": self.cell_delta,
            "state": self.state,
            "error": None,
        }


class PaceexWifiClient:
    """Read-only TCP-клієнт Wi‑Fi модуля PACEEX на LP16-24200."""

    def __init__(self, host: str, port: int = DEFAULT_PORT, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.host = host.strip()
        self.port = port
        self.timeout = timeout

    def _query_once(self, query: bytes) -> bytes:
        try:
            with socket.create_connection((self.host, self.port), self.timeout) as sock:
                sock.settimeout(self.timeout)
                sock.sendall(query)
                response = bytearray()
                while not response.endswith(b"\x9d"):
                    chunk = sock.recv(4096)
                    if not chunk:
                        break
                    response.extend(chunk)
        except (OSError, TimeoutError) as exc:
            raise BatteryConnectionError(
                f"немає відповіді від {self.host}:{self.port} — {exc}"
            ) from exc

        frame = bytes(response)
        if len(frame) < 11 or frame[0] != 0x9A or frame[-1] != 0x9D:
            raise BatteryProtocolError(f"некоректний кадр PACEEX: {frame.hex()}")
        expected = 11 + frame[7]
        if len(frame) != expected:
            raise BatteryProtocolError(
                f"довжина кадру {len(frame)}, очікувалось {expected}"
            )
        received = int.from_bytes(frame[-3:-1], "big")
        if crc16(frame[:-3]) != received:
            raise BatteryProtocolError("помилка CRC кадру PACEEX")
        return frame

    def _query(self, query: bytes) -> bytes:
        last_error: Exception | None = None
        for attempt in range(len(QUERY_RETRY_DELAYS) + 1):
            try:
                return self._query_once(query)
            except BatteryError as exc:
                last_error = exc
                if attempt == len(QUERY_RETRY_DELAYS):
                    break
                time.sleep(QUERY_RETRY_DELAYS[attempt])
        assert last_error is not None
        raise last_error

    def read_serial(self) -> str | None:
        try:
            response = self._query(SERIAL_QUERY)
        except BatteryError:
            return None
        length = response[8]
        serial = response[9 : 9 + length].decode("ascii", errors="replace").strip("\x00 ")
        return serial or None

    def read(self) -> BatteryReading:
        status = self._query(STATUS_QUERY)
        time.sleep(QUERY_COOLDOWN)
        cells_frame = self._query(CELLS_QUERY)

        cell_count = cells_frame[11]
        if not 1 <= cell_count <= 32:
            raise BatteryProtocolError(f"некоректна кількість комірок: {cell_count}")

        cells = [
            int.from_bytes(cells_frame[12 + index * 4 : 14 + index * 4], "big") / 1000
            for index in range(cell_count)
        ]
        current = int.from_bytes(status[9:13], "big", signed=True) / 100
        voltage = int.from_bytes(status[13:17], "big") / 100
        serial: str | None = None
        try:
            time.sleep(QUERY_COOLDOWN)
            serial = self.read_serial()
        except BatteryError:
            serial = None
        return BatteryReading(
            host=self.host,
            port=self.port,
            serial=serial,
            soc=int(status[29]),
            soh=int(status[30]),
            voltage=round(voltage, 2),
            current=round(current, 2),
            power=round(voltage * current, 1),
            remaining_ah=round(int.from_bytes(status[17:21], "big") / 100, 2),
            design_ah=round(int.from_bytes(status[21:25], "big") / 100, 2),
            cycles=int.from_bytes(status[31:35], "big"),
            cell_count=cell_count,
            cells=[round(value, 3) for value in cells],
            cell_min=round(min(cells), 3),
            cell_max=round(max(cells), 3),
            cell_delta=round(max(cells) - min(cells), 3),
        )


def _guess_lan_ip() -> str | None:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except OSError:
        return None


def _probe_host(host: str, port: int, timeout: float) -> str | None:
    client = PaceexWifiClient(host, port, timeout)
    try:
        frame = client._query_once(STATUS_QUERY)
    except BatteryError:
        return None
    if frame[0] == 0x9A and frame[-1] == 0x9D:
        return host
    return None


def scan_network(port: int = DEFAULT_PORT, timeout: float = SCAN_TIMEOUT) -> list[str]:
    lan_ip = _guess_lan_ip()
    if not lan_ip:
        raise BatteryConnectionError("не вдалося визначити адресу локальної мережі")
    prefix = ".".join(lan_ip.split(".")[:3])
    found: list[str] = []
    with ThreadPoolExecutor(max_workers=SCAN_WORKERS) as pool:
        futures = {
            pool.submit(_probe_host, f"{prefix}.{last}", port, timeout): last
            for last in range(1, 255)
            if f"{prefix}.{last}" != lan_ip
        }
        for future in as_completed(futures):
            host = future.result()
            if host:
                found.append(host)
    found.sort(key=lambda item: tuple(int(part) for part in item.split(".")))
    return found


def read_cached(host: str, port: int = DEFAULT_PORT, timeout: float = DEFAULT_TIMEOUT) -> dict[str, Any]:
    key = f"{host}:{port}"
    now = time.monotonic()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < CACHE_TTL:
            return dict(hit[1])
    try:
        payload = PaceexWifiClient(host, port, timeout).read().as_dict()
    except BatteryError as exc:
        payload = {
            "ok": False,
            "host": host,
            "port": port,
            "error": str(exc),
        }
    with _cache_lock:
        _cache[key] = (time.monotonic(), payload)
    return dict(payload)


def print_report(reading: BatteryReading) -> None:
    print("╔" + "═" * 70 + "╗")
    print("║  MUST LP16-24200  ·  PACEEX BMS  ·  Wi‑Fi TCP".ljust(71) + "║")
    print("╚" + "═" * 70 + "╝")
    print(f"  Хост            {reading.host}:{reading.port}")
    if reading.serial:
        print(f"  Серійний номер  {reading.serial}")
    print(f"  Стан            {reading.state}")
    print(f"  SOC             {reading.soc} %")
    print(f"  SOH             {reading.soh} %")
    print(f"  Напруга         {reading.voltage:.2f} В")
    print(f"  Струм           {reading.current:.2f} А")
    print(f"  Потужність      {reading.power:.1f} Вт")
    print(f"  Залишок         {reading.remaining_ah:.2f} А·год")
    print(f"  Ємність         {reading.design_ah:.2f} А·год")
    print(f"  Цикли           {reading.cycles}")
    print(f"  Комірки         {reading.cell_count} шт  Δ {reading.cell_delta:.3f} В")
    print(f"  Мін / макс      {reading.cell_min:.3f} / {reading.cell_max:.3f} В")
    print("  Напруги комірок")
    for index, voltage in enumerate(reading.cells, 1):
        bar_len = max(0, min(24, int((voltage - 2.8) / 0.85 * 24)))
        bar = "█" * bar_len + "░" * (24 - bar_len)
        print(f"    {index:02d}  {voltage:5.3f} В  {bar}")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="MUST LP16-24200 — читання BMS через Wi‑Fi (PACEEX TCP :8888)."
    )
    parser.add_argument("--host", help="IP Wi‑Fi модуля батареї в локальній мережі")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="TCP-порт (типово 8888)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="Таймаут, с")
    parser.add_argument("--scan", action="store_true", help="Знайти модуль у підмережі (порт 8888)")
    parser.add_argument("--watch", type=float, metavar="SEC", help="Оновлювати дані кожні SEC секунд")
    parser.add_argument("--json", action="store_true", dest="as_json", help="Вивести JSON")
    parser.add_argument("--web", action="store_true", help="Веб-дашборд з даними АКБ")
    parser.add_argument("--lan", action="store_true", help="Веб на 0.0.0.0")
    parser.add_argument("--http-port", type=int, default=8080, dest="http_port")
    parser.add_argument("--bind", default="127.0.0.1", help="Адреса веб-сервера")
    return parser.parse_args(argv)


def _print_not_found() -> None:
    print("Батарею в мережі не знайдено.")
    print()
    print("Зроби так (один раз, з телефону):")
    print("  1. Встанови додаток BMS-Tool або Paceex BMS.")
    print("  2. Стань біля батареї, увімкни Bluetooth.")
    print("  3. Підключись до батареї і в налаштуваннях мережі вкажи")
    print("     домашній Wi‑Fi (лише 2.4 ГГц, не 5 ГГц). Пароль адміна часто 4321.")
    print("  4. Закрий додаток — інакше ноут не підключиться.")
    print("  5. Ноут і батарея мають бути в одній Wi‑Fi мережі. Запусти пошук знову.")


def run_scan(port: int, quiet: bool = False) -> list[str]:
    if not quiet:
        print(f"Пошук батареї в домашньому Wi‑Fi (порт {port})…")
    found = scan_network(port)
    if not found:
        _print_not_found()
        return []
    for host in found:
        print(f"Знайдено: {host}:{port}")
    return found


def start_web_dashboard(
    host: str,
    port: int = DEFAULT_PORT,
    timeout: float = DEFAULT_TIMEOUT,
    lan: bool = False,
    bind: str = "127.0.0.1",
    http_port: int = 8080,
    open_browser: bool = True,
) -> int:
    import must_settings as core
    from must_web import run_web

    url = f"http://127.0.0.1:{http_port}/"
    print()
    print(f"Відкриваю сторінку в браузері: {url}")
    print("Це вікно не закривай — поки воно відкрите, дані оновлюються.")
    print("Ctrl+C — зупинити.")
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()

    web_args = argparse.Namespace(
        port=core.DEFAULT_PORT,
        baud=core.DEFAULT_BAUD,
        slave=core.DEFAULT_SLAVE,
        timeout=timeout,
        bms_host=host,
        bms_port=port,
        bms_only=True,
        lan=lan,
        host="0.0.0.0" if lan else bind,
        http_port=http_port,
    )
    run_web(web_args)
    return 0


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def _pick_host(found: list[str]) -> str | None:
    if not found:
        return None
    if len(found) == 1:
        print(f"Беремо {found[0]}")
        return found[0]
    print("Знайдено кілька пристроїв:")
    for index, host in enumerate(found, 1):
        print(f"  {index}) {host}")
    choice = _ask("Номер або IP: ")
    if choice.isdigit() and 1 <= int(choice) <= len(found):
        return found[int(choice) - 1]
    return choice or found[0]


def interactive_menu() -> int:
    print()
    print("MUST LP16-24200 — перегляд батареї на ноуті")
    print("Ноут і батарея мають бути в одній Wi‑Fi мережі.")
    print()
    print("  1) Знайти батарею і відкрити в браузері   ← почни з цього")
    print("  2) Відкрити сторінку за відомою IP-адресою")
    print("  3) Показати цифри тут, у цьому вікні")
    print("  0) Вихід")
    print()
    choice = _ask("Твій вибір [1]: ") or "1"

    if choice == "0":
        return 0

    host = ""
    try:
        if choice == "2":
            host = _ask("IP батареї (як у роутері, напр. 192.168.1.50): ")
        elif choice in {"1", "3"}:
            found = run_scan(DEFAULT_PORT)
            host = _pick_host(found) or ""
        else:
            print("Невідомий пункт.")
            return 3
    except BatteryError as exc:
        print(f"Помилка сканування: {exc}")
        return 1

    if not host:
        print("Немає IP батареї.")
        return 3

    if choice == "3":
        try:
            print_report(PaceexWifiClient(host).read())
            _ask("\nEnter — закрити…")
            return 0
        except BatteryError as exc:
            print(f"Немає з'єднання: {exc}")
            print("Закрий BMS-Tool / Paceex BMS на телефоні і спробуй ще раз.")
            return 1

    return start_web_dashboard(host, lan=True)


def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = parse_args(argv)
    if argv is None and len(sys.argv) == 1:
        return interactive_menu()

    if args.scan:
        try:
            found = run_scan(args.port)
        except BatteryError as exc:
            print(f"Помилка сканування: {exc}", file=sys.stderr)
            return 1
        return 0 if found else 2

    if not args.host:
        return interactive_menu()

    if args.web:
        return start_web_dashboard(
            args.host,
            args.port,
            args.timeout,
            lan=args.lan,
            bind=args.bind,
            http_port=args.http_port,
        )

    def once() -> BatteryReading:
        return PaceexWifiClient(args.host, args.port, args.timeout).read()

    try:
        if args.watch:
            while True:
                print("\033[2J\033[H", end="")
                reading = once()
                if args.as_json:
                    print(json.dumps(reading.as_dict(), ensure_ascii=False, indent=2))
                else:
                    print_report(reading)
                print(f"\nОновлення кожні {args.watch:g} с. Ctrl+C — вихід.")
                time.sleep(args.watch)
        reading = once()
        if args.as_json:
            print(json.dumps(reading.as_dict(), ensure_ascii=False, indent=2))
        else:
            print_report(reading)
        return 0
    except BatteryConnectionError as exc:
        print(f"Немає з'єднання: {exc}", file=sys.stderr)
        print("Перевір IP, що модуль у 2.4 ГГц Wi‑Fi, і що BMS-Tool не тримає сесію.", file=sys.stderr)
        return 1
    except BatteryProtocolError as exc:
        print(f"Помилка протоколу: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nЗупинено.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
