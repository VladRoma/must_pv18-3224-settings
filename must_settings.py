#!/usr/bin/env python3
"""Читання та відображення налаштувань інвертора MUST PV18-3224 по Modbus RTU."""

from __future__ import annotations

import argparse
import struct
import sys
import time

import serial

DEFAULT_PORT = "COM8"
DEFAULT_BAUD = 19200
DEFAULT_SLAVE = 4
DEFAULT_TIMEOUT = 1.0
PAUSE_BETWEEN_READS = 0.25

# --- Modbus RTU -------------------------------------------------------------


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


class ModbusRtuError(RuntimeError):
    pass


class ModbusRtuClient:
    def __init__(self, port: str, baudrate: int, slave: int, timeout: float) -> None:
        self.slave = slave
        self.ser = serial.Serial(
            port=port,
            baudrate=baudrate,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=timeout,
            write_timeout=timeout,
        )

    def close(self) -> None:
        if self.ser and self.ser.is_open:
            self.ser.close()

    def read_holding(self, address: int, count: int) -> list[int]:
        request = struct.pack(">BBHH", self.slave, 0x03, address, count)
        request += struct.pack("<H", crc16(request))

        self.ser.reset_input_buffer()
        self.ser.write(request)
        self.ser.flush()

        header = self._read_exact(3)
        unit, function, byte_or_exc = header
        if unit != self.slave:
            raise ModbusRtuError(f"неочікувана адреса slave: {unit}")

        if function & 0x80:
            crc_bytes = self._read_exact(2)
            frame = header + crc_bytes
            self._check_crc(frame)
            raise ModbusRtuError(f"Modbus exception {byte_or_exc} на регістрі {address}")

        if function != 0x03:
            raise ModbusRtuError(f"неочікувана функція {function}")

        payload = self._read_exact(byte_or_exc + 2)
        frame = header + payload
        self._check_crc(frame)

        data = payload[:-2]
        if len(data) != byte_or_exc or byte_or_exc != count * 2:
            raise ModbusRtuError(
                f"неповна відповідь: очікувалось {count * 2} байт, отримано {len(data)}"
            )
        return list(struct.unpack(f">{count}H", data))

    def _read_exact(self, size: int) -> bytes:
        data = self.ser.read(size)
        if len(data) != size:
            raise ModbusRtuError(
                f"таймаут COM-порту (отримано {len(data)} з {size} байт)"
            )
        return data

    @staticmethod
    def _check_crc(frame: bytes) -> None:
        body, received = frame[:-2], struct.unpack("<H", frame[-2:])[0]
        expected = crc16(body)
        if received != expected:
            raise ModbusRtuError(
                f"помилка CRC: очікувалось {expected:04X}, отримано {received:04X}"
            )


# --- Декодування ------------------------------------------------------------


def i16(value: int) -> int:
    return value - 0x10000 if value & 0x8000 else value


def scale(value: int, factor: float, digits: int = 1) -> float:
    return round(i16(value) * factor, digits)


def on_off(value: int) -> str:
    return "Увімкнено" if value else "Вимкнено"


def mapped(value: int, table: dict[int, str]) -> str:
    return table.get(value, f"Невідомо ({value})")


def decode_model(hi: int, lo: int) -> str:
    chars = "".join(
        chr(b) for b in ((hi >> 8) & 0xFF, hi & 0xFF) if 32 <= b <= 126
    )
    return f"{chars}{lo}" if chars else str((hi << 16) | lo)


def decode_serial(hi: int, lo: int) -> str:
    value = (hi << 16) | lo
    if value in (0, 0xFFFFFFFF):
        return "не задано"
    return str(value)


def decode_version(value: int) -> str:
    return f"{value // 10000}.{(value // 100) % 100}.{value % 100}"


ENERGY_USE_MODE = {
    1: "SBU (сонячна / АКБ / мережа)",
    2: "SUB (сонячна / мережа / АКБ)",
    3: "UTI (лише мережа)",
    4: "SOL (лише сонячна)",
}

GRID_PROTECT = {
    0: "VDE (184–253 В)",
    1: "UPS (170–280 В)",
    2: "APL (90–280 В)",
    3: "GEN (генератор)",
}

SOLAR_USE_AIM = {
    0: "LBU (спочатку навантаження)",
    1: "BLU (спочатку АКБ)",
}

CHARGER_SOURCE = {
    0: "CSO (спочатку сонячна)",
    1: "CUB (спочатку мережа)",
    2: "SNU (сонячна + мережа)",
    3: "OSO (лише сонячна)",
}

# Програма 14: AGM / FLD / USE / LI
BATTERY_TYPE = {
    0: "AGM",
    1: "FLD (заливна)",
    2: "USE (користувацька)",
    3: "LI (літій)",
}

WORK_STATE = {
    0: "Увімкнення",
    1: "Самотестування",
    2: "Автономний (Off-Grid)",
    3: "Мережевий (Grid-Tie)",
    4: "Обхід (Bypass)",
    5: "Зупинка",
    6: "Заряд від мережі",
}


# --- Читання блоків ---------------------------------------------------------

RegisterMap = dict[int, int]


def read_block(client: ModbusRtuClient, start: int, count: int) -> RegisterMap:
    values = client.read_holding(start, count)
    time.sleep(PAUSE_BETWEEN_READS)
    return {start + i: values[i] for i in range(count)}


def read_all(client: ModbusRtuClient) -> dict[str, RegisterMap]:
    data = {
        "device": read_block(client, 20000, 6),
        "charger": read_block(client, 10101, 24),
        # VPM II: програми 17–18, 19/38/39 — у розширеному блоці 20122–20148
        "inverter": read_block(client, 20101, 54),
        "live": read_block(client, 25201, 80),
        "pv": read_block(client, 15201, 20),
    }
    try:
        data["bms"] = read_block(client, 109, 8)
    except ModbusRtuError:
        data["bms"] = {}
    try:
        data["bms_ext"] = read_block(client, 44100, 90)
    except ModbusRtuError:
        data["bms_ext"] = {}
    return data


# --- Структуровані дані для CLI / GUI ---------------------------------------

Row = dict[str, object]
Section = dict[str, object]


def _row(
    label: str,
    value: object,
    unit: str = "",
    program: int | None = None,
    register: int | None = None,
) -> Row:
    return {
        "program": program,
        "label": label,
        "value": value,
        "unit": unit,
        "register": register,
    }


def decode_bms_errors(raw: int) -> str:
    if raw == 0:
        return "немає"
    return f"код {raw}"


def build_sections(data: dict[str, RegisterMap]) -> list[Section]:
    d, c, inv, live = data["device"], data["charger"], data["inverter"], data["live"]
    bms = data.get("bms", {})
    pv = data.get("pv", {})
    bms_ext = data.get("bms_ext", {})
    model = decode_model(d[20000], d[20001])
    sys_bits = inv.get(20142, 0)
    batt_i = i16(live.get(25274, 0))
    batt_p = i16(live.get(25273, 0))

    sections: list[Section] = [
        {
            "title": "Пристрій і поточний стан",
            "rows": [
                _row("Модель", model),
                _row("Серійний номер", decode_serial(d[20002], d[20003])),
                _row("Апаратна версія", decode_version(d[20004])),
                _row("Прошивка", decode_version(d[20005])),
                _row("Modbus-протокол", decode_version(live.get(25278, 0))),
                _row("Номінальна потужність", live.get(25203, 0), "Вт", register=25203),
                _row("Робочий стан", mapped(live.get(25201, 0), WORK_STATE), register=25201),
                _row("Напруга АКБ (інвертор)", fmt_volt(live.get(25205, 0)), "В", register=25205),
                _row("Струм АКБ (інвертор)", f"{batt_i:.1f}", "А", register=25274),
                _row("Потужність АКБ (інвертор)", f"{batt_p}", "Вт", register=25273),
            ],
        },
        {
            "title": "АКБ через CAN / BMS",
            "rows": [
                _row("BMS напруга", fmt_volt(bms.get(109, 0)), "В", register=109),
                _row("BMS струм", f"{scale(bms.get(110, 0), 0.1):.1f}", "А", register=110),
                _row("BMS температура", bms.get(111, "—"), "°C", register=111),
                _row("BMS SOC", bms.get(113, "—"), "%", register=113),
                _row("BMS SOH", bms.get(114, "—"), "%", register=114),
                _row("BMS помилки", decode_bms_errors(bms.get(112, 0)), register=112),
                _row(
                    "SOC з CAN-блоку (44180)",
                    bms_ext.get(44180, "—"),
                    "%",
                    register=44180,
                ),
            ],
        },
        {
            "title": "PV, мережа, навантаження",
            "rows": [
                _row("PV напруга", fmt_volt(pv.get(15205, 0)), "В", register=15205),
                _row("PV струм", fmt_volt(pv.get(15207, 0)), "А", register=15207),
                _row("PV потужність", pv.get(15208, "—"), "Вт", register=15208),
                _row("PV MPPT", pv.get(15202, "—"), register=15202),
                _row("PV заряд", pv.get(15203, "—"), register=15203),
                _row("PV температура", pv.get(15209, "—"), "°C", register=15209),
                _row("Напруга мережі", fmt_volt(live.get(25207, 0)), "В", register=25207),
                _row("Струм мережі", fmt_volt(live.get(25211, 0)), "А", register=25211),
                _row("Потужність мережі", i16(live.get(25214, 0)), "Вт", register=25214),
                _row("Частота мережі", f"{scale(live.get(25226, 0), 0.01, 2):.2f}", "Гц", register=25226),
                _row("Напруга навантаження", fmt_volt(live.get(25206, 0)), "В", register=25206),
                _row("Струм навантаження", fmt_volt(live.get(25212, 0)), "А", register=25212),
                _row("Потужність навантаження", i16(live.get(25215, 0)), "Вт", register=25215),
                _row("Завантаження", f"{scale(live.get(25216, 0), 1):.0f}", "%", register=25216),
                _row("Темп. AC радіатора", live.get(25233, "—"), "°C", register=25233),
                _row("Темп. DC радіатора", live.get(25235, "—"), "°C", register=25235),
            ],
        },
        {
            "title": "Пріоритети та мережа",
            "rows": [
                _row("Пріоритет вихідного джерела", mapped(inv[20109], ENERGY_USE_MODE), program=1, register=20109),
                _row("Діапазон вхідної AC-напруги", mapped(inv[20111], GRID_PROTECT), program=2, register=20111),
                _row("Вихідна напруга", fmt_volt(inv[20102]), "В", program=3, register=20102),
                _row("Вихідна частота", f"{scale(inv[20103], 0.01, 2):.2f}", "Гц", program=4, register=20103),
                _row("Пріоритет сонячної енергії", mapped(inv[20112], SOLAR_USE_AIM), program=5, register=20112),
                _row("Обхід при перевантаженні", lcd_bit(sys_bits, 0, "bYE", "bYd"), program=6, register=20142),
                _row("Автоперезапуск після перевантаження", lcd_bit(sys_bits, 2, "LtE", "Ltd"), program=7, register=20142),
                _row("Автоперезапуск після перегріву", lcd_bit(sys_bits, 3, "ttE", "ttd"), program=8, register=20142),
                _row("Пріоритет джерела заряджання", mapped(inv[20143], CHARGER_SOURCE), program=10, register=20143),
            ],
        },
        {
            "title": "Заряджання",
            "rows": [
                _row("Макс. струм заряджання", fmt_volt(inv[20132]), "А", program=11, register=20132),
                _row("Струм заряджання від мережі", fmt_volt(inv[20125]), "А", program=13, register=20125),
                _row("Тип акумулятора", mapped(c[10110], BATTERY_TYPE), program=14, register=10110),
            ],
        },
        {
            "title": "Напруги акумулятора (USE / LI)",
            "rows": [
                _row("Напруга насичення (Bulk / C.V.)", fmt_volt(inv[20123]), "В", program=17, register=20123),
                _row("Напруга буферного заряду (Float)", fmt_volt(inv[20122]), "В", program=18, register=20122),
                _row(
                    "Відсікання низької SOC" if soc_mode(inv) else "Відсікання низької DC-напруги",
                    fmt_pct(inv[20146]) if soc_mode(inv) else fmt_volt(inv[20127]),
                    "%" if soc_mode(inv) else "В",
                    program=19,
                    register=20146 if soc_mode(inv) else 20127,
                ),
                _row("Стоп розряду АКБ (з мережею)", fmt_volt(inv[20118]), "В", program=20, register=20118),
                _row("Стоп заряду АКБ", fmt_volt(inv[20119]), "В", program=21, register=20119),
            ],
        },
        {
            "title": "Дисплей і сигналізація",
            "rows": [
                _row("Автоперегортання сторінок", lcd_bit(sys_bits, 5, "PTE", "PTd"), program=22, register=20142),
                _row("Підсвітка дисплея", lcd_bit(sys_bits, 4, "LON", "LOF"), program=23, register=20142),
                _row("Звуковий сигнал", lcd_bit(sys_bits, 1, "bON", "bOF"), program=24, register=20142),
                _row("Сигнал при втраті джерела", lcd_bit(sys_bits, 7, "AON", "AOF"), program=25, register=20142),
                _row("Запис кодів несправностей", lcd_bit(sys_bits, 6, "FON", "FOF"), program=27, register=20142),
                _row("Баланс сонячної потужності", "SbE" if inv.get(20144, 0) else "Sbd", program=28, register=20144),
            ],
        },
        {
            "title": "Вирівнювання (Equalization)",
            "rows": [
                _row("Вирівнювання акумулятора", "EEN" if c[10118] else "EdS", program=30, register=10118),
                _row("Напруга вирівнювання", fmt_volt(c[10119]), "В", program=31, register=10119),
                _row("Час вирівнювання", c[10121], "хв", program=33, register=10121),
                _row("Таймаут вирівнювання", c[10122], "хв", program=34, register=10122),
                _row("Інтервал вирівнювання", c[10123], "днів", program=35, register=10123),
            ],
        },
        {
            "title": "BMS / літій (LI)",
            "rows": [
                _row("Метод контролю BMS", mapped(inv.get(20137, 0), BMS_METHOD), program=37, register=20137),
                _row("SOC — стоп розряду", fmt_pct(inv[20147]), "%", program=38, register=20147),
                _row("SOC — стоп заряду", fmt_pct(inv[20148]), "%", program=39, register=20148),
                _row("Зв'язок з BMS", mapped(inv.get(20140, 0), BMS_COMM), program=40, register=20140),
                _row("Протокол літієвої АКБ", inv.get(20141, 0), program=41, register=20141),
            ],
        },
    ]
    return sections


# --- Відображення CLI -------------------------------------------------------

WIDTH = 72

# VPM II: 0 = SOC, 1 = VOL (перевірено на PV18-3224)
BMS_METHOD = {0: "SOC (відсоток)", 1: "VOL (напруга)"}
# Програма 40: 0 = Uni, 1 = LDP (перевірено на PV18-3224 VPM II)
BMS_COMM = {
    0: "Uni (не продовжувати без BMS)",
    1: "LDP (продовжувати без BMS)",
}


class Report:
    def __init__(self) -> None:
        self._lines: list[str] = []

    def banner(self, title: str) -> None:
        inner = f" {title} "
        pad = WIDTH - 2 - len(inner)
        left = pad // 2
        self._lines.append("╔" + "═" * (WIDTH - 2) + "╗")
        self._lines.append("║" + " " * left + inner + " " * (pad - left) + "║")
        self._lines.append("╚" + "═" * (WIDTH - 2) + "╝")

    def section(self, title: str) -> None:
        self._lines.append("")
        self._lines.append(f"┌─ {title} " + "─" * max(0, WIDTH - len(title) - 5))

    def end_section(self) -> None:
        self._lines.append("└" + "─" * (WIDTH - 2))

    def info(self, label: str, value: object, unit: str = "") -> None:
        text = f"{value} {unit}".strip()
        self._lines.append(f"│  {label:<34} {text}")

    def setting(self, program: int, name: str, value: object, unit: str = "") -> None:
        text = f"{value} {unit}".strip()
        tag = f"[{program:02d}]"
        self._lines.append(f"│  {tag} {name:<30} {text}")

    def dump(self) -> None:
        print("\n".join(self._lines))


def lcd_bit(raw: int, bit: int, on: str, off: str) -> str:
    return on if raw & (1 << bit) else off


def soc_mode(inv: RegisterMap) -> bool:
    return inv.get(20137, 1) == 0


def fmt_volt(raw: int) -> str:
    return f"{scale(raw, 0.1):.1f}"


def fmt_pct(raw: int) -> str:
    return str(i16(raw))


def build_report(data: dict[str, RegisterMap]) -> None:
    r = Report()
    r.banner("MUST PV18-3224 VPM II — налаштування LCD")
    for section in build_sections(data):
        r.section(str(section["title"]))
        for row in section["rows"]:
            prog = row.get("program")
            label = str(row["label"])
            value = row["value"]
            unit = str(row.get("unit") or "")
            if prog is not None:
                r.setting(int(prog), label, value, unit)
            else:
                r.info(label, value, unit)
        r.end_section()
    c, inv = data["charger"], data["inverter"]
    sys_bits = inv.get(20142, 0)
    r.section("Modbus (додатково, не LCD)")
    r.info("Ємність АКБ", c[10111], "А·год")
    r.info("Макс. струм розряду", fmt_volt(inv[20113]), "А")
    r.info("Зарядник Float (10103)", fmt_volt(c[10103]), "В")
    r.info("Зарядник Bulk (10104)", fmt_volt(c[10104]), "В")
    r.info("Системний регістр 20142", f"0x{sys_bits:04X} ({sys_bits})")
    r.end_section()
    r.dump()


# --- CLI --------------------------------------------------------------------


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Відображення налаштувань MUST PV18-3224 через COM-порт (Modbus RTU)."
    )
    parser.add_argument("--port", default=DEFAULT_PORT, help="COM-порт (типово COM8)")
    parser.add_argument("--baud", type=int, default=DEFAULT_BAUD, help="Швидкість (типово 19200)")
    parser.add_argument("--slave", type=int, default=DEFAULT_SLAVE, help="Modbus-адреса (типово 4)")
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT, help="Таймаут, с")
    parser.add_argument(
        "--watch",
        type=float,
        metavar="SEC",
        help="Оновлювати дані кожні SEC секунд",
    )
    parser.add_argument("--gui", action="store_true", help="Відкрити віконну програму (tkinter)")
    parser.add_argument("--web", action="store_true", help="Веб-інтерфейс (http://127.0.0.1:8080)")
    parser.add_argument("--host", default="127.0.0.1", help="Адреса веб-сервера")
    parser.add_argument("--http-port", type=int, default=8080, dest="http_port", help="Порт веб-сервера")
    return parser.parse_args()


def connect_and_read(args: argparse.Namespace) -> dict[str, RegisterMap]:
    client = ModbusRtuClient(args.port, args.baud, args.slave, args.timeout)
    try:
        return read_all(client)
    finally:
        client.close()


def probe_if_needed(args: argparse.Namespace) -> dict[str, RegisterMap]:
    last_error: Exception | None = None
    attempts: list[tuple[int, int]] = [(args.baud, args.slave)]
    for baud in (19200, 9600):
        for slave in (4, 1):
            combo = (baud, slave)
            if combo not in attempts:
                attempts.append(combo)

    for baud, slave in attempts:
        probe_args = argparse.Namespace(**vars(args))
        probe_args.baud = baud
        probe_args.slave = slave
        try:
            data = connect_and_read(probe_args)
            if (baud, slave) != (args.baud, args.slave):
                print(f"Підключено як {args.port}, {baud} бод, slave {slave}")
            args.baud = baud
            args.slave = slave
            return data
        except (ModbusRtuError, serial.SerialException) as exc:
            last_error = exc
    assert last_error is not None
    raise last_error


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    args = parse_args()
    if args.gui:
        from must_gui import run_gui

        run_gui(args)
        return 0

    if args.web:
        from must_web import run_web

        run_web(args)
        return 0

    print(f"MUST PV18-3224  |  {args.port}  |  {args.baud} 8N1  |  slave {args.slave}")
    print("Лище читання регістрів, нічого не записується.")

    def once() -> None:
        data = probe_if_needed(args)
        build_report(data)
        print()

    try:
        if args.watch:
            while True:
                print("\033[2J\033[H", end="")
                once()
                print(f"Оновлення кожні {args.watch:g} с. Ctrl+C — вихід.")
                time.sleep(args.watch)
        else:
            once()
    except serial.SerialException as exc:
        print(f"Не вдалося відкрити {args.port}: {exc}", file=sys.stderr)
        print("Закрийте WatchPower / інші програми, що тримають порт.", file=sys.stderr)
        return 1
    except ModbusRtuError as exc:
        print(f"Помилка зв'язку з інвертором: {exc}", file=sys.stderr)
        print("Перевірте кабель, що інвертор увімкнений, baud 19200 і адресу 4.", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("\nЗупинено.")
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
