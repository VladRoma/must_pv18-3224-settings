#!/usr/bin/env python3
"""Telegram-сповіщення MUST: мережа, заряджання АКБ."""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

_ENV_LOADED = False
STATE_FILE = Path(__file__).resolve().parent / ".must-telegram-state.json"

DEFAULT_INTERVAL_SEC = 45
DEFAULT_GRID_V_MIN = 180.0
DEFAULT_CHARGE_W_MIN = 80.0
DEFAULT_DEBOUNCE_SAMPLES = 3


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


def _env_int(name: str, default: int, *, minimum: int = 1) -> int:
    load_env()
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return max(minimum, int(raw))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    load_env()
    raw = (os.environ.get(name) or "").strip()
    if not raw:
        return default
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return default


def poll_interval_sec() -> int:
    return _env_int("TELEGRAM_INTERVAL_SEC", DEFAULT_INTERVAL_SEC, minimum=15)


def debounce_samples() -> int:
    return _env_int("TELEGRAM_DEBOUNCE_SAMPLES", DEFAULT_DEBOUNCE_SAMPLES, minimum=1)


def grid_v_min() -> float:
    return _env_float("TELEGRAM_GRID_V_MIN", DEFAULT_GRID_V_MIN)


def charge_w_min() -> float:
    return _env_float("TELEGRAM_CHARGE_W_MIN", DEFAULT_CHARGE_W_MIN)


def telegram_enabled(args: Any) -> bool:
    load_env()
    if getattr(args, "telegram", False):
        return True
    token = (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()
    chat = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    return bool(token and chat)


def chat_ids() -> list[str]:
    load_env()
    raw = (os.environ.get("TELEGRAM_CHAT_ID") or "").strip()
    if not raw:
        return []
    return [part.strip() for part in raw.split(",") if part.strip()]


def bot_token() -> str:
    load_env()
    return (os.environ.get("TELEGRAM_BOT_TOKEN") or "").strip()


def status_line(args: Any) -> str | None:
    if not telegram_enabled(args):
        return None
    ids = chat_ids()
    sec = poll_interval_sec()
    return f"Telegram сповіщення  →  chat {ids[0]}{'…' if len(ids) > 1 else ''} · кожні {sec} с"


def _load_state() -> dict[str, Any]:
    if not STATE_FILE.is_file():
        return {"update_offset": 0}
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {"update_offset": 0}


def _save_state(state: dict[str, Any]) -> None:
    try:
        STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        STATE_FILE.chmod(0o600)
    except OSError:
        pass


def _api_request(token: str, method: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    url = f"https://api.telegram.org/bot{token}/{method}"
    data = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method="POST" if data else "GET")
    with urllib.request.urlopen(req, timeout=25) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    if not body.get("ok"):
        raise RuntimeError(body.get("description") or "Telegram API error")
    return body


def _send_to_chat(chat_id: str, text: str, *, silent: bool = False) -> None:
    token = bot_token()
    if not token or not chat_id:
        return
    load_env()
    silent_env = (os.environ.get("TELEGRAM_SILENT") or "").strip().lower() in ("1", "true", "yes")
    _api_request(
        token,
        "sendMessage",
        {
            "chat_id": chat_id,
            "text": text,
            "disable_notification": silent or silent_env,
        },
    )


def send_message(text: str, *, silent: bool = False) -> None:
    for chat_id in chat_ids():
        _send_to_chat(chat_id, text, silent=silent)


def _format_status(payload: dict[str, Any]) -> str:
    metrics = payload.get("metrics") or {}
    numbers = payload.get("numbers") or {}

    def m(key: str) -> str:
        row = metrics.get(key) or {}
        val = row.get("value", "—")
        unit = row.get("unit") or ""
        if val == "—" or not unit:
            return str(val)
        return f"{val} {unit}".strip()

    lines = [
        "MUST · поточний стан",
        f"Стан: {m('state')}",
        f"SOC: {m('soc')}",
        f"PV: {m('pv_p')}",
        f"Навантаження: {m('load_p')}",
        f"Мережа: {m('grid_p')}",
        f"АКБ: {m('batt_p')}",
    ]
    gv = numbers.get("grid_v")
    if gv is not None:
        lines.append(f"U мережі: {gv:.0f} V")
    lines.append(f"Оновлено: {payload.get('updated', '—')}")
    return "\n".join(lines)


def _snapshot(payload: dict[str, Any]) -> dict[str, Any]:
    numbers = payload.get("numbers") or {}
    metrics = payload.get("metrics") or {}
    state_row = metrics.get("state") or {}
    state_text = str(state_row.get("value") or "")
    grid_v = numbers.get("grid_v")
    batt_w = numbers.get("batt_w")
    grid_ok: bool | None
    if grid_v is None:
        grid_ok = None
    else:
        grid_ok = grid_v >= grid_v_min()
    charging = batt_w is not None and batt_w >= charge_w_min()
    if "Заряд" in state_text:
        charging = True
    return {
        "grid_ok": grid_ok,
        "charging": charging,
        "grid_v": grid_v,
        "batt_w": batt_w,
        "pv_w": numbers.get("pv_w"),
        "load_w": numbers.get("load_w"),
        "grid_w": numbers.get("grid_w"),
        "soc": numbers.get("soc"),
        "state": state_text or "—",
    }


def _apply_debounce(
    state: dict[str, Any],
    key: str,
    sample: bool,
) -> bool | None:
    """Повертає нове стабільне значення після debounce або None, якщо без змін."""
    stable_key = f"{key}_stable"
    streak_key = f"{key}_streak"
    want_key = f"{key}_want"

    stable = state.get(stable_key)
    streak = int(state.get(streak_key) or 0)
    want = state.get(want_key)

    if want is sample:
        streak += 1
    else:
        want = sample
        streak = 1

    state[want_key] = want
    state[streak_key] = streak

    if streak < debounce_samples():
        state[stable_key] = stable
        return None

    if stable is None:
        state[stable_key] = sample
        return None

    if stable == sample:
        return None

    state[stable_key] = sample
    return sample


def _notify_grid_change(was_ok: bool, snap: dict[str, Any]) -> None:
    soc = snap.get("soc")
    soc_s = f"{soc:.0f}%" if soc is not None else "—"
    load_w = snap.get("load_w")
    load_s = f"{load_w:.0f} W" if load_w is not None else "—"
    if was_ok:
        send_message(
            "⚠️ Зникла мережа (220 V).\n"
            f"Режим: {snap.get('state', '—')}\n"
            f"SOC: {soc_s} · навантаження: {load_s}"
        )
    else:
        gv = snap.get("grid_v")
        gv_s = f"{gv:.0f} V" if gv is not None else "—"
        send_message(
            "✅ Мережа відновлена.\n"
            f"U мережі: {gv_s} · SOC: {soc_s} · навантаження: {load_s}"
        )


def _notify_charging(snap: dict[str, Any]) -> None:
    batt_w = snap.get("batt_w")
    pv_w = snap.get("pv_w")
    grid_w = snap.get("grid_w")
    soc = snap.get("soc")
    parts = [f"Потужність АКБ: {batt_w:.0f} W" if batt_w is not None else "Потужність АКБ: —"]
    if pv_w is not None:
        parts.append(f"PV: {pv_w:.0f} W")
    if grid_w is not None:
        parts.append(f"Мережа: {grid_w:.0f} W")
    if soc is not None:
        parts.append(f"SOC: {soc:.0f}%")
    send_message("🔋 Йде заряджання АКБ.\n" + " · ".join(parts))


def _process_updates(token: str, offset: int, allowed: set[str], args: Any) -> int:
    query = urllib.parse.urlencode({"timeout": 0, "offset": offset, "allowed_updates": json.dumps(["message"])})
    url = f"https://api.telegram.org/bot{token}/getUpdates?{query}"
    try:
        with urllib.request.urlopen(url, timeout=25) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
        print(f"[telegram] getUpdates: {exc}", flush=True)
        return offset

    if not body.get("ok"):
        return offset

    for item in body.get("result") or []:
        offset = max(offset, int(item.get("update_id", 0)) + 1)
        message = item.get("message") or {}
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id", ""))
        text = (message.get("text") or "").strip()
        if not chat_id or not text:
            continue

        if allowed and chat_id not in allowed:
            continue

        if text.startswith("/start"):
            _send_to_chat(
                chat_id,
                f"Chat ID: {chat_id}\n"
                "Додай у .env на Pi: TELEGRAM_CHAT_ID=" + chat_id + "\n"
                "Перезапусти must-web. Команди: /status — показники MUST.",
            )
            continue

        if text.startswith("/status"):
            try:
                from must_web import build_payload

                payload = build_payload(args)
                _send_to_chat(chat_id, _format_status(payload))
            except Exception as exc:
                _send_to_chat(chat_id, f"Не вдалося зчитати інвертор: {exc}")

    return offset


def _telegram_loop(args: Any, stop: threading.Event) -> None:
    from must_web import build_payload

    token = bot_token()
    allowed = set(chat_ids())
    state = _load_state()
    interval = poll_interval_sec()

    time.sleep(20)

    while not stop.is_set():
        if token:
            state["update_offset"] = _process_updates(token, int(state.get("update_offset") or 0), allowed, args)

        try:
            payload = build_payload(args)
            conn = payload.get("connection") or {}
            if conn.get("inverter_error") and not payload.get("sections"):
                raise RuntimeError(conn.get("inverter_error") or "немає даних інвертора")

            snap = _snapshot(payload)
            if snap["grid_ok"] is not None:
                new_grid = _apply_debounce(state, "grid", snap["grid_ok"])
                if new_grid is not None:
                    _notify_grid_change(not new_grid, snap)

            new_charge = _apply_debounce(state, "charge", snap["charging"])
            if new_charge is True:
                _notify_charging(snap)

            state["last_ok"] = payload.get("updated")
            state.pop("comm_notified", None)
        except Exception as exc:
            if not state.get("comm_notified"):
                send_message(f"⚠️ MUST: немає актуальних даних інвертора.\n{exc}")
                state["comm_notified"] = True
            print(f"[telegram] пропуск: {exc}", flush=True)

        _save_state(state)
        if stop.wait(interval):
            break


_stop = threading.Event()
_thread: threading.Thread | None = None


def start_background(args: Any) -> None:
    global _thread
    if not telegram_enabled(args):
        return
    if not bot_token() or not chat_ids():
        print("[telegram] потрібні TELEGRAM_BOT_TOKEN і TELEGRAM_CHAT_ID у .env", flush=True)
        return
    _stop.clear()
    _thread = threading.Thread(
        target=_telegram_loop,
        args=(args, _stop),
        name="telegram-notifier",
        daemon=True,
    )
    _thread.start()


def stop_background() -> None:
    _stop.set()
