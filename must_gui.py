#!/usr/bin/env python3
"""Віконний монітор MUST PV18-3224 VPM II."""

from __future__ import annotations

import argparse
import math
import re
import time
import tkinter as tk
import tkinter.font as tkfont
from collections import deque
from datetime import datetime
from tkinter import messagebox, ttk

import serial

import must_settings as core

REFRESH_MS = 5000
HISTORY_MAX_POINTS = 720  # ~1 год при оновленні кожні 5 с

TAB_OVERVIEW = "Огляд"
TAB_CHART = "Графік"
TAB_BMS = "АКБ / CAN"
TAB_TELEMETRY = "Енергія"
TAB_SETTINGS = "Налаштування"

SETTING_SECTIONS = {
    TAB_SETTINGS: {
        "Пріоритети та мережа",
        "Заряджання",
        "Напруги акумулятора (USE / LI)",
        "Дисплей і сигналізація",
        "Вирівнювання (Equalization)",
        "BMS / літій (LI)",
    },
}


class Theme:
    BG = "#070b10"
    SURFACE = "#0f141c"
    CARD = "#151c28"
    CARD_ALT = "#121822"
    CARD_HOVER = "#1a2433"
    BORDER = "#243044"
    TEXT = "#f1f5f9"
    MUTED = "#7d92ad"
    ACCENT = "#f59e0b"
    BATTERY = "#34d399"
    SOLAR = "#fcd34d"
    GRID = "#60a5fa"
    LOAD = "#f472b6"
    INFO = "#38bdf8"
    OK = "#4ade80"
    WARN = "#fb923c"
    ERROR = "#f87171"
    FLOW_LINE = "#334155"
    FLOW_ACTIVE = "#64748b"


def parse_number(text: str) -> float | None:
    match = re.search(r"-?\d+(?:[.,]\d+)?", str(text))
    if not match:
        return None
    return float(match.group(0).replace(",", "."))


class EnergyFlowCanvas(tk.Canvas):
    """Схема потоків: PV · АКБ · Мережа · Навантаження."""

    NODE_R = 42

    def __init__(self, master: tk.Widget, **kwargs) -> None:
        super().__init__(
            master,
            bg=Theme.CARD,
            highlightthickness=0,
            height=250,
            **kwargs,
        )
        self.bind("<Configure>", self._on_resize)
        self._values = {
            "pv": ("—", Theme.SOLAR),
            "grid": ("—", Theme.GRID),
            "load": ("—", Theme.LOAD),
            "batt": ("—", Theme.BATTERY),
            "soc": ("—", Theme.BATTERY),
        }

    def _on_resize(self, _event: tk.Event | None = None) -> None:
        self._redraw()

    def update_values(
        self,
        pv_w: str,
        grid_w: str,
        load_w: str,
        batt_w: str,
        soc: str,
    ) -> None:
        self._values = {
            "pv": (pv_w, Theme.SOLAR),
            "grid": (grid_w, Theme.GRID),
            "load": (load_w, Theme.LOAD),
            "batt": (batt_w, Theme.BATTERY),
            "soc": (soc, Theme.BATTERY),
        }
        self._redraw()

    def _center(self) -> tuple[int, int]:
        return self.winfo_width() // 2, self.winfo_height() // 2 + 8

    def _node_pos(self) -> dict[str, tuple[int, int]]:
        cx, cy = self._center()
        pad_x = max(90, self.winfo_width() // 5)
        pad_y = max(58, self.winfo_height() // 4)
        return {
            "pv": (cx, cy - pad_y - 18),
            "grid": (cx - pad_x, cy + pad_y - 10),
            "load": (cx + pad_x, cy + pad_y - 10),
            "batt": (cx, cy),
        }

    def _flow_strength(self, text: str) -> float:
        val = parse_number(text)
        if val is None:
            return 0.0
        return min(abs(val) / 3000.0, 1.0)

    def _draw_arrow(
        self,
        x1: int,
        y1: int,
        x2: int,
        y2: int,
        color: str,
        strength: float,
        reverse: bool = False,
    ) -> None:
        if reverse:
            x1, y1, x2, y2 = x2, y2, x1, y1
        width = 2 + int(strength * 4)
        line_color = color if strength > 0.05 else Theme.FLOW_LINE
        self.create_line(x1, y1, x2, y2, fill=line_color, width=width, smooth=True)

        angle = math.atan2(y2 - y1, x2 - x1)
        ah = 10 + int(strength * 4)
        ax = x2 - ah * math.cos(angle - 0.35)
        ay = y2 - ah * math.sin(angle - 0.35)
        bx = x2 - ah * math.cos(angle + 0.35)
        by = y2 - ah * math.sin(angle + 0.35)
        self.create_polygon(x2, y2, ax, ay, bx, by, fill=line_color, outline="")

    def _draw_node(
        self,
        key: str,
        x: int,
        y: int,
        icon: str,
        title: str,
        value: str,
        color: str,
        *,
        center: bool = False,
    ) -> None:
        r = self.NODE_R if not center else self.NODE_R + 6
        fill = Theme.CARD_ALT if not center else "#182030"
        outline = color if center else Theme.BORDER
        width = 2 if center else 1
        self.create_oval(x - r, y - r, x + r, y + r, fill=fill, outline=outline, width=width)
        self.create_text(x, y - 14, text=icon, fill=color, font=("Segoe UI Emoji", 16))
        self.create_text(x, y + 8, text=title, fill=Theme.MUTED, font=("Segoe UI", 9))
        self.create_text(x, y + 26, text=value, fill=color, font=("Segoe UI", 11, "bold"))

    def _redraw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 40 or h < 40:
            return

        pos = self._node_pos()
        cx, cy = pos["batt"]

        pv_val, pv_c = self._values["pv"]
        grid_val, grid_c = self._values["grid"]
        load_val, load_c = self._values["load"]
        batt_val, batt_c = self._values["batt"]
        soc_val, soc_c = self._values["soc"]

        grid_num = parse_number(grid_val) or 0.0
        batt_num = parse_number(batt_val) or 0.0

        self._draw_arrow(
            pos["pv"][0], pos["pv"][1] + self.NODE_R,
            cx, cy - self.NODE_R - 6,
            pv_c, self._flow_strength(pv_val),
        )
        self._draw_arrow(
            pos["grid"][0] + self.NODE_R, pos["grid"][1],
            cx - self.NODE_R - 4, cy + 8,
            grid_c, self._flow_strength(grid_val),
            reverse=grid_num < 0,
        )
        self._draw_arrow(
            cx + self.NODE_R + 4, cy + 8,
            pos["load"][0] - self.NODE_R, pos["load"][1],
            load_c, self._flow_strength(load_val),
        )
        if abs(batt_num) > 5:
            charge = batt_num < 0
            y_from = cy + self.NODE_R + 8 if charge else cy - self.NODE_R - 8
            y_to = cy - self.NODE_R - 8 if charge else cy + self.NODE_R + 8
            self._draw_arrow(cx, y_from, cx, y_to, batt_c, self._flow_strength(batt_val))

        self.create_text(
            w // 2, 14,
            text="ПОТОК ЕНЕРГІЇ",
            fill=Theme.MUTED,
            font=("Segoe UI", 9, "bold"),
        )

        self._draw_node("pv", *pos["pv"], "☀", "PV", pv_val, pv_c)
        self._draw_node("grid", *pos["grid"], "⚡", "Мережа", grid_val, grid_c)
        self._draw_node("load", *pos["load"], "🏠", "Навант.", load_val, load_c)
        batt_label = f"{batt_val}\n{soc_val}"
        self._draw_node("batt", cx, cy, "🔋", "АКБ", batt_label, soc_c, center=True)


class HistoryChart(tk.Canvas):
    """Лінійний графік з rolling-історією."""

    PAD_L = 52
    PAD_R = 16
    PAD_T = 34
    PAD_B = 38

    def __init__(
        self,
        master: tk.Widget,
        title: str,
        y_unit: str,
        *,
        y_min: float | None = None,
        y_max: float | None = None,
        fill_series: str | None = None,
        height: int = 220,
        show_legend: bool = True,
        **kwargs,
    ) -> None:
        super().__init__(master, bg=Theme.CARD, highlightthickness=0, height=height, **kwargs)
        self._title = title
        self._y_unit = y_unit
        self._y_min_fixed = y_min
        self._y_max_fixed = y_max
        self._fill_series = fill_series
        self._show_legend = show_legend
        self._series: dict[str, dict] = {}
        self.bind("<Configure>", self._on_resize)

    def define_series(self, key: str, label: str, color: str) -> None:
        self._series[key] = {
            "label": label,
            "color": color,
            "visible": True,
            "points": deque(maxlen=HISTORY_MAX_POINTS),
        }

    def append(self, key: str, value: float | None, ts: float | None = None) -> None:
        if key not in self._series or value is None:
            return
        self._series[key]["points"].append((ts or time.time(), value))
        self._redraw()

    def set_visible(self, key: str, visible: bool) -> None:
        if key in self._series:
            self._series[key]["visible"] = visible
            self._redraw()

    def clear(self) -> None:
        for meta in self._series.values():
            meta["points"].clear()
        self._redraw()

    def point_count(self) -> int:
        counts = [len(m["points"]) for m in self._series.values()]
        return max(counts) if counts else 0

    def _on_resize(self, _event: tk.Event | None = None) -> None:
        self._redraw()

    def _plot_rect(self) -> tuple[int, int, int, int]:
        w, h = self.winfo_width(), self.winfo_height()
        return self.PAD_L, self.PAD_T, w - self.PAD_R, h - self.PAD_B

    def _time_bounds(self) -> tuple[float, float] | None:
        times: list[float] = []
        for meta in self._series.values():
            if meta["visible"]:
                times.extend(t for t, _ in meta["points"])
        if len(times) < 2:
            return None
        return min(times), max(times)

    def _value_bounds(self) -> tuple[float, float] | None:
        if self._y_min_fixed is not None and self._y_max_fixed is not None:
            return self._y_min_fixed, self._y_max_fixed

        values: list[float] = []
        for meta in self._series.values():
            if not meta["visible"]:
                continue
            values.extend(v for _, v in meta["points"])
        if not values:
            return None

        lo, hi = min(values), max(values)
        if lo == hi:
            pad = max(abs(lo) * 0.1, 1.0)
            return lo - pad, hi + pad
        pad = (hi - lo) * 0.08
        return lo - pad, hi + pad

    def _map_x(self, ts: float, t0: float, t1: float, x0: int, x1: int) -> int:
        if t1 <= t0:
            return x0
        ratio = (ts - t0) / (t1 - t0)
        return int(x0 + ratio * (x1 - x0))

    def _map_y(self, val: float, vmin: float, vmax: float, y0: int, y1: int) -> int:
        if vmax <= vmin:
            return (y0 + y1) // 2
        ratio = (val - vmin) / (vmax - vmin)
        return int(y1 - ratio * (y1 - y0))

    def _redraw(self) -> None:
        self.delete("all")
        w, h = self.winfo_width(), self.winfo_height()
        if w < 80 or h < 60:
            return

        x0, y0, x1, y1 = self._plot_rect()
        self.create_text(14, 16, text=self._title, anchor="w", fill=Theme.MUTED, font=("Segoe UI", 9, "bold"))

        t_bounds = self._time_bounds()
        v_bounds = self._value_bounds()
        if not t_bounds or not v_bounds:
            self.create_text(
                w // 2,
                h // 2,
                text="Накопичення даних…",
                fill=Theme.MUTED,
                font=("Segoe UI", 11),
            )
            return

        t0, t1 = t_bounds
        vmin, vmax = v_bounds

        for i in range(5):
            frac = i / 4
            y = int(y1 - frac * (y1 - y0))
            self.create_line(x0, y, x1, y, fill=Theme.BORDER, dash=(2, 6))
            val = vmin + frac * (vmax - vmin)
            self.create_text(x0 - 8, y, text=f"{val:.0f}", anchor="e", fill=Theme.MUTED, font=("Segoe UI", 8))

        self.create_text(x0 - 8, y0 - 10, text=self._y_unit, anchor="sw", fill=Theme.MUTED, font=("Segoe UI", 8))

        for i in range(5):
            frac = i / 4
            x = int(x0 + frac * (x1 - x0))
            ts = t0 + frac * (t1 - t0)
            self.create_text(x, y1 + 14, text=datetime.fromtimestamp(ts).strftime("%H:%M"), anchor="n", fill=Theme.MUTED, font=("Segoe UI", 8))

        self.create_line(x0, y0, x0, y1, fill=Theme.BORDER)
        self.create_line(x0, y1, x1, y1, fill=Theme.BORDER)

        if self._fill_series and self._fill_series in self._series:
            meta = self._series[self._fill_series]
            if meta["visible"] and len(meta["points"]) >= 2:
                pts = list(meta["points"])
                fill_coords: list[int] = []
                for ts, val in pts:
                    fill_coords.extend([self._map_x(ts, t0, t1, x0, x1), self._map_y(val, vmin, vmax, y0, y1)])
                fill_coords.extend([self._map_x(pts[-1][0], t0, t1, x0, x1), y1, self._map_x(pts[0][0], t0, t1, x0, x1), y1])
                self.create_polygon(*fill_coords, fill="#1a3d32", outline="")

        for key, meta in self._series.items():
            if not meta["visible"]:
                continue
            pts = list(meta["points"])
            if len(pts) < 2:
                continue
            coords: list[int] = []
            for ts, val in pts:
                coords.extend([self._map_x(ts, t0, t1, x0, x1), self._map_y(val, vmin, vmax, y0, y1)])
            line_w = 2.5 if key == self._fill_series else 2
            self.create_line(*coords, fill=meta["color"], width=line_w, smooth=True)
            lx, ly = coords[-2], coords[-1]
            self.create_oval(lx - 3, ly - 3, lx + 3, ly + 3, fill=meta["color"], outline=Theme.CARD)

        if self._show_legend:
            legend_x = x1 - 8
            for meta in reversed(list(self._series.values())):
                if not meta["visible"]:
                    continue
                legend_x -= 90
                self.create_line(legend_x, 16, legend_x + 18, 16, fill=meta["color"], width=2)
                self.create_text(legend_x + 24, 16, text=meta["label"], anchor="w", fill=Theme.MUTED, font=("Segoe UI", 8))


class MetricCard(tk.Frame):
    def __init__(
        self,
        master: tk.Widget,
        title: str,
        subtitle: str,
        accent: str,
        *,
        large: bool = False,
    ) -> None:
        super().__init__(
            master,
            bg=Theme.CARD,
            highlightthickness=1,
            highlightbackground=Theme.BORDER,
        )
        stripe = tk.Frame(self, bg=accent, height=3)
        stripe.pack(fill="x")

        inner = tk.Frame(self, bg=Theme.CARD)
        inner.pack(fill="both", expand=True, padx=14, pady=12)

        font_value = ("Segoe UI", 22 if large else 18, "bold")
        tk.Label(inner, text=title, bg=Theme.CARD, fg=Theme.MUTED, font=("Segoe UI", 10)).pack(anchor="w")
        self.value_lbl = tk.Label(inner, text="—", bg=Theme.CARD, fg=accent, font=font_value)
        self.value_lbl.pack(anchor="w", pady=(6, 2))
        self.sub_lbl = tk.Label(inner, text=subtitle, bg=Theme.CARD, fg=Theme.MUTED, font=("Segoe UI", 9))
        self.sub_lbl.pack(anchor="w")

    def set(self, value: str, subtitle: str | None = None) -> None:
        self.value_lbl.configure(text=value)
        if subtitle is not None:
            self.sub_lbl.configure(text=subtitle)


class MustMonitorApp(tk.Tk):
    def __init__(self, args: argparse.Namespace) -> None:
        super().__init__()
        self.args = args
        self.title("MUST PV18 · VPM II Monitor")
        self.geometry("1180x800")
        self.minsize(980, 660)
        self.configure(bg=Theme.BG)

        self._refresh_job: str | None = None
        self._status = "idle"
        self._sections: list[core.Section] = []

        self._fonts = {
            "title": tkfont.Font(family="Segoe UI", size=22, weight="bold"),
            "subtitle": tkfont.Font(family="Segoe UI", size=10),
            "metric": tkfont.Font(family="Segoe UI", size=30, weight="bold"),
            "metric_sm": tkfont.Font(family="Segoe UI", size=18, weight="bold"),
            "label": tkfont.Font(family="Segoe UI", size=10),
            "badge": tkfont.Font(family="Segoe UI", size=9, weight="bold"),
        }

        self._setup_styles()
        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(300, self.refresh)

    def _setup_styles(self) -> None:
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        style.configure(".", background=Theme.BG, foreground=Theme.TEXT, borderwidth=0)
        style.configure("TFrame", background=Theme.BG)
        style.configure("TLabel", background=Theme.BG, foreground=Theme.TEXT)

        style.configure(
            "Accent.TButton",
            background=Theme.ACCENT,
            foreground="#111827",
            padding=(16, 9),
            font=("Segoe UI", 10, "bold"),
            borderwidth=0,
        )
        style.map(
            "Accent.TButton",
            background=[("active", "#d97706"), ("pressed", "#b45309"), ("disabled", "#374151")],
            foreground=[("disabled", "#6b7280")],
        )

        style.configure("TNotebook", background=Theme.BG, borderwidth=0, tabmargins=[2, 6, 2, 0])
        style.configure(
            "TNotebook.Tab",
            background=Theme.SURFACE,
            foreground=Theme.MUTED,
            padding=(18, 11),
            font=("Segoe UI", 10, "bold"),
            borderwidth=0,
        )
        style.map(
            "TNotebook.Tab",
            background=[("selected", Theme.CARD), ("active", Theme.CARD_HOVER)],
            foreground=[("selected", Theme.TEXT), ("active", Theme.TEXT)],
            expand=[("selected", [1, 1, 1, 0])],
        )

        style.configure(
            "Custom.Treeview",
            background=Theme.CARD,
            fieldbackground=Theme.CARD,
            foreground=Theme.TEXT,
            borderwidth=0,
            rowheight=32,
            font=("Segoe UI", 10),
        )
        style.configure(
            "Custom.Treeview.Heading",
            background=Theme.SURFACE,
            foreground=Theme.MUTED,
            relief="flat",
            font=("Segoe UI", 9, "bold"),
            padding=(8, 6),
        )
        style.map(
            "Custom.Treeview",
            background=[("selected", Theme.CARD_HOVER)],
            foreground=[("selected", Theme.TEXT)],
        )

        style.configure(
            "Dark.Vertical.TScrollbar",
            background=Theme.SURFACE,
            troughcolor=Theme.BG,
            borderwidth=0,
            arrowcolor=Theme.MUTED,
        )
        style.map(
            "Dark.Vertical.TScrollbar",
            background=[("active", Theme.BORDER), ("pressed", Theme.ACCENT)],
        )

        style.configure(
            "Dark.TCheckbutton",
            background=Theme.BG,
            foreground=Theme.TEXT,
            font=("Segoe UI", 9),
        )
        style.map(
            "Dark.TCheckbutton",
            background=[("active", Theme.BG), ("selected", Theme.BG)],
            foreground=[("disabled", Theme.MUTED)],
        )

    def _build_ui(self) -> None:
        self._header = tk.Frame(self, bg=Theme.SURFACE, highlightthickness=1, highlightbackground=Theme.BORDER)
        self._header.pack(fill="x")
        self._build_header(self._header)

        body = tk.Frame(self, bg=Theme.BG)
        body.pack(fill="both", expand=True, padx=16, pady=(12, 8))

        self.notebook = ttk.Notebook(body)
        self.notebook.pack(fill="both", expand=True)

        self._overview_tab = tk.Frame(self.notebook, bg=Theme.BG)
        self._chart_tab = tk.Frame(self.notebook, bg=Theme.BG)
        self._bms_tab = tk.Frame(self.notebook, bg=Theme.BG)
        self._telemetry_tab = tk.Frame(self.notebook, bg=Theme.BG)
        self._settings_tab = tk.Frame(self.notebook, bg=Theme.BG)

        self.notebook.add(self._overview_tab, text=f"  {TAB_OVERVIEW}  ")
        self.notebook.add(self._chart_tab, text=f"  {TAB_CHART}  ")
        self.notebook.add(self._bms_tab, text=f"  {TAB_BMS}  ")
        self.notebook.add(self._telemetry_tab, text=f"  {TAB_TELEMETRY}  ")
        self.notebook.add(self._settings_tab, text=f"  {TAB_SETTINGS}  ")

        self._build_overview_tab()
        self._build_chart_tab()
        self._trees: dict[str, ttk.Treeview] = {}
        self._build_bms_tab()
        self._trees[TAB_TELEMETRY] = self._make_tree(self._telemetry_tab)
        self._trees[TAB_SETTINGS] = self._make_tree(self._settings_tab)

        self._footer = tk.Frame(self, bg=Theme.SURFACE, highlightthickness=1, highlightbackground=Theme.BORDER)
        self._footer.pack(fill="x")
        self._build_footer(self._footer)

    def _build_header(self, parent: tk.Frame) -> None:
        inner = tk.Frame(parent, bg=Theme.SURFACE)
        inner.pack(fill="x", padx=20, pady=16)

        brand = tk.Frame(inner, bg=Theme.ACCENT, width=4)
        brand.pack(side="left", fill="y", padx=(0, 14))
        brand.pack_propagate(False)

        left = tk.Frame(inner, bg=Theme.SURFACE)
        left.pack(side="left", fill="x", expand=True)

        tk.Label(left, text="MUST PV18-3224", bg=Theme.SURFACE, fg=Theme.TEXT, font=self._fonts["title"]).pack(anchor="w")
        tk.Label(
            left,
            text="VPM II · Modbus RTU Monitor",
            bg=Theme.SURFACE,
            fg=Theme.ACCENT,
            font=("Segoe UI", 10, "bold"),
        ).pack(anchor="w", pady=(2, 0))
        self.conn_var = tk.StringVar(value=f"{self.args.port}  ·  {self.args.baud} 8N1  ·  slave {self.args.slave}")
        tk.Label(left, textvariable=self.conn_var, bg=Theme.SURFACE, fg=Theme.MUTED, font=self._fonts["subtitle"]).pack(
            anchor="w", pady=(4, 0)
        )

        right = tk.Frame(inner, bg=Theme.SURFACE)
        right.pack(side="right")

        self.status_pill = tk.Label(
            right,
            text="● Очікування",
            bg="#1e293b",
            fg=Theme.MUTED,
            font=self._fonts["badge"],
            padx=14,
            pady=7,
        )
        self.status_pill.pack(side="right", padx=(12, 0))

        refresh_btn = ttk.Button(right, text="↻  Оновити", style="Accent.TButton", command=self.refresh)
        refresh_btn.pack(side="right")

    def _build_footer(self, parent: tk.Frame) -> None:
        inner = tk.Frame(parent, bg=Theme.SURFACE)
        inner.pack(fill="x", padx=20, pady=11)
        self.time_var = tk.StringVar(value="Оновлення: —")
        tk.Label(inner, textvariable=self.time_var, bg=Theme.SURFACE, fg=Theme.MUTED, font=self._fonts["subtitle"]).pack(
            side="left"
        )
        tk.Label(
            inner,
            text="Лише читання · CAN/BMS через інвертор",
            bg=Theme.SURFACE,
            fg=Theme.MUTED,
            font=self._fonts["subtitle"],
        ).pack(side="right")

    def _build_overview_tab(self) -> None:
        top = tk.Frame(self._overview_tab, bg=Theme.BG)
        top.pack(fill="x", pady=(0, 10))

        hero = tk.Frame(top, bg=Theme.CARD, highlightthickness=1, highlightbackground=Theme.BORDER, width=300)
        hero.pack(side="left", fill="y", padx=(0, 10))
        hero.pack_propagate(False)
        tk.Frame(hero, bg=Theme.BATTERY, height=3).pack(fill="x")

        hero_inner = tk.Frame(hero, bg=Theme.CARD)
        hero_inner.pack(fill="both", expand=True, padx=18, pady=16)

        tk.Label(hero_inner, text="ЗАРЯД АКБ", bg=Theme.CARD, fg=Theme.MUTED, font=self._fonts["badge"]).pack(anchor="w")
        self.soc_value = tk.Label(hero_inner, text="—", bg=Theme.CARD, fg=Theme.BATTERY, font=self._fonts["metric"])
        self.soc_value.pack(anchor="w", pady=(2, 10))

        bar_wrap = tk.Frame(hero_inner, bg=Theme.BORDER, height=12)
        bar_wrap.pack(fill="x", pady=(0, 14))
        bar_wrap.pack_propagate(False)
        self.soc_bar = tk.Frame(bar_wrap, bg=Theme.BATTERY, height=12)
        self.soc_bar.place(relx=0, rely=0, relheight=1, relwidth=0.0)

        self.hero_details = tk.Label(
            hero_inner,
            text="—",
            bg=Theme.CARD,
            fg=Theme.TEXT,
            font=self._fonts["subtitle"],
            justify="left",
        )
        self.hero_details.pack(anchor="w")

        self.state_badge = tk.Label(
            hero_inner,
            text="—",
            bg="#1e293b",
            fg=Theme.INFO,
            font=("Segoe UI", 11, "bold"),
            padx=10,
            pady=6,
        )
        self.state_badge.pack(anchor="w", pady=(14, 0))

        flow_wrap = tk.Frame(top, bg=Theme.CARD, highlightthickness=1, highlightbackground=Theme.BORDER)
        flow_wrap.pack(side="left", fill="both", expand=True)
        self.flow_canvas = EnergyFlowCanvas(flow_wrap)
        self.flow_canvas.pack(fill="both", expand=True, padx=2, pady=2)

        info_row = tk.Frame(self._overview_tab, bg=Theme.BG)
        info_row.pack(fill="x", pady=(0, 10))
        self._info_cards: list[MetricCard] = []
        for title, subtitle, accent in [
            ("Модель", "пристрій", Theme.INFO),
            ("Прошивка", "версія", Theme.MUTED),
            ("Потужність", "номінал", Theme.ACCENT),
            ("Протокол", "Modbus", Theme.GRID),
        ]:
            card = MetricCard(info_row, title, subtitle, accent)
            card.pack(side="left", fill="both", expand=True, padx=(0, 8))
            self._info_cards.append(card)
        self._info_cards[-1].pack_configure(padx=0)

        grid = tk.Frame(self._overview_tab, bg=Theme.BG)
        grid.pack(fill="both", expand=True)

        self._overview_cards: dict[str, MetricCard] = {}
        cards = [
            ("☀  PV", "pv_p", Theme.SOLAR, "сонячна генерація"),
            ("⚡  Навантаження", "load_p", Theme.LOAD, "споживання"),
            ("🔌  Мережа", "grid_p", Theme.GRID, "обмін з мережею"),
            ("🔋  BMS U", "bms_v", Theme.BATTERY, "напруга CAN"),
            ("📊  BMS I", "bms_i", Theme.BATTERY, "струм CAN"),
            ("🌡  BMS T", "bms_t", Theme.INFO, "температура"),
            ("⚙  Тип АКБ", "bat_type", Theme.ACCENT, "програма 14"),
            ("📡  BMS SOH", "bms_soh", Theme.BATTERY, "стан здоров'я"),
        ]

        for idx, (title, key, color, subtitle) in enumerate(cards):
            card = MetricCard(grid, title, subtitle, color)
            card.grid(row=idx // 4, column=idx % 4, padx=5, pady=5, sticky="nsew")
            self._overview_cards[key] = card

        for col in range(4):
            grid.columnconfigure(col, weight=1)
        for row in range(2):
            grid.rowconfigure(row, weight=1)

    def _build_chart_tab(self) -> None:
        toolbar = tk.Frame(self._chart_tab, bg=Theme.CARD, highlightthickness=1, highlightbackground=Theme.BORDER)
        toolbar.pack(fill="x", pady=(0, 10))
        tk.Frame(toolbar, bg=Theme.ACCENT, height=3).pack(fill="x")

        bar = tk.Frame(toolbar, bg=Theme.CARD)
        bar.pack(fill="x", padx=14, pady=10)

        self._chart_info_var = tk.StringVar(value="Точок: 0  ·  історія до ~1 год")
        tk.Label(bar, textvariable=self._chart_info_var, bg=Theme.CARD, fg=Theme.MUTED, font=self._fonts["subtitle"]).pack(
            side="left"
        )

        ttk.Button(bar, text="Очистити", style="Accent.TButton", command=self._clear_charts).pack(side="right")

        toggles = tk.Frame(bar, bg=Theme.CARD)
        toggles.pack(side="right", padx=(0, 16))
        self._power_toggles: dict[str, tk.BooleanVar] = {}
        for key, label, color in [
            ("pv", "PV", Theme.SOLAR),
            ("load", "Load", Theme.LOAD),
            ("grid", "Grid", Theme.GRID),
            ("batt", "АКБ", Theme.BATTERY),
        ]:
            var = tk.BooleanVar(value=True)
            self._power_toggles[key] = var
            cb = tk.Checkbutton(
                toggles,
                text=label,
                variable=var,
                bg=Theme.CARD,
                fg=color,
                selectcolor=Theme.SURFACE,
                activebackground=Theme.CARD,
                activeforeground=color,
                highlightthickness=0,
                bd=0,
                font=("Segoe UI", 9, "bold"),
                command=lambda k=key, v=var: self._power_chart.set_visible(k, v.get()),
            )
            cb.pack(side="left", padx=(0, 10))

        soc_wrap = tk.Frame(self._chart_tab, bg=Theme.CARD, highlightthickness=1, highlightbackground=Theme.BORDER)
        soc_wrap.pack(fill="both", expand=True, pady=(0, 10))
        self._soc_chart = HistoryChart(soc_wrap, "BMS SOC", "%", y_min=0, y_max=100, fill_series="soc", height=240)
        self._soc_chart.define_series("soc", "SOC", Theme.BATTERY)
        self._soc_chart.pack(fill="both", expand=True, padx=2, pady=2)

        power_wrap = tk.Frame(self._chart_tab, bg=Theme.CARD, highlightthickness=1, highlightbackground=Theme.BORDER)
        power_wrap.pack(fill="both", expand=True)
        self._power_chart = HistoryChart(power_wrap, "ПОТУЖНІСТЬ", "Вт", height=260, show_legend=False)
        self._power_chart.define_series("pv", "PV", Theme.SOLAR)
        self._power_chart.define_series("load", "Load", Theme.LOAD)
        self._power_chart.define_series("grid", "Grid", Theme.GRID)
        self._power_chart.define_series("batt", "АКБ", Theme.BATTERY)
        self._power_chart.pack(fill="both", expand=True, padx=2, pady=2)

    def _clear_charts(self) -> None:
        self._soc_chart.clear()
        self._power_chart.clear()
        self._chart_info_var.set("Точок: 0  ·  історія до ~1 год")

    def _append_history(self) -> None:
        now = time.time()
        soc = parse_number(self._row_value("BMS SOC")[0])
        pv = parse_number(self._row_value("PV потужність")[0])
        load = parse_number(self._row_value("Потужність навантаження")[0])
        grid = parse_number(self._row_value("Потужність мережі")[0])
        batt = parse_number(self._row_value("Потужність АКБ (інвертор)")[0])

        self._soc_chart.append("soc", soc, now)
        self._power_chart.append("pv", pv, now)
        self._power_chart.append("load", load, now)
        self._power_chart.append("grid", grid, now)
        self._power_chart.append("batt", batt, now)

        n = self._soc_chart.point_count()
        span_min = max(1, int(n * REFRESH_MS / 60000))
        self._chart_info_var.set(f"Точок: {n}  ·  ~{span_min} хв історії  ·  макс. {HISTORY_MAX_POINTS}")

    def _build_bms_tab(self) -> None:
        banner = tk.Frame(self._bms_tab, bg=Theme.CARD, highlightthickness=1, highlightbackground=Theme.BORDER)
        banner.pack(fill="x", pady=(0, 10))
        tk.Frame(banner, bg=Theme.BATTERY, height=3).pack(fill="x")
        tk.Label(
            banner,
            text="CAN  →  інвертор  →  Modbus   ·   регістри 109–114",
            bg=Theme.CARD,
            fg=Theme.MUTED,
            font=self._fonts["subtitle"],
            padx=16,
            pady=12,
        ).pack(anchor="w")

        cards_row = tk.Frame(self._bms_tab, bg=Theme.BG)
        cards_row.pack(fill="x", pady=(0, 10))
        self._bms_cards: dict[str, MetricCard] = {}
        for title, key, accent in [
            ("Напруга BMS", "bms_v", Theme.BATTERY),
            ("Струм BMS", "bms_i", Theme.BATTERY),
            ("SOC", "bms_soc", Theme.OK),
            ("SOH", "bms_soh", Theme.INFO),
            ("Температура", "bms_t", Theme.WARN),
            ("Помилки", "bms_err", Theme.ERROR),
        ]:
            card = MetricCard(cards_row, title, "CAN", accent, large=True)
            card.pack(side="left", fill="both", expand=True, padx=(0, 8))
            self._bms_cards[key] = card
        self._bms_cards["bms_err"].pack_configure(padx=0)

        self._trees[TAB_BMS] = self._make_tree(self._bms_tab)

    def _make_tree(self, parent: tk.Widget) -> ttk.Treeview:
        wrap = tk.Frame(parent, bg=Theme.BG)
        wrap.pack(fill="both", expand=True)

        columns = ("program", "label", "value", "unit", "register")
        tree = ttk.Treeview(wrap, columns=columns, show="headings", style="Custom.Treeview")
        tree.heading("program", text="Пр.")
        tree.heading("label", text="Параметр")
        tree.heading("value", text="Значення")
        tree.heading("unit", text="Од.")
        tree.heading("register", text="Reg")
        tree.column("program", width=56, anchor="center", stretch=False)
        tree.column("label", width=380, anchor="w")
        tree.column("value", width=280, anchor="w")
        tree.column("unit", width=64, anchor="center", stretch=False)
        tree.column("register", width=72, anchor="center", stretch=False)

        scroll = ttk.Scrollbar(wrap, orient="vertical", command=tree.yview, style="Dark.Vertical.TScrollbar")
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        tree.tag_configure("section", foreground=Theme.ACCENT, background=Theme.SURFACE, font=("Segoe UI", 10, "bold"))
        tree.tag_configure("odd", background=Theme.CARD)
        tree.tag_configure("even", background=Theme.CARD_ALT)
        return tree

    def _set_status(self, state: str, text: str) -> None:
        colors = {
            "idle": ("#1e293b", Theme.MUTED),
            "loading": ("#422006", Theme.ACCENT),
            "ok": ("#052e16", Theme.OK),
            "error": ("#450a0a", Theme.ERROR),
        }
        bg, fg = colors.get(state, colors["idle"])
        self.status_pill.configure(text=f"● {text}", bg=bg, fg=fg)
        self._status = state

    def _on_close(self) -> None:
        if self._refresh_job:
            self.after_cancel(self._refresh_job)
        self.destroy()

    def refresh(self) -> None:
        if self._refresh_job:
            self.after_cancel(self._refresh_job)
            self._refresh_job = None

        self._set_status("loading", "Читання…")
        self.update_idletasks()
        try:
            data = core.probe_if_needed(self.args)
            self._sections = core.build_sections(data)
            self.conn_var.set(f"{self.args.port}  ·  {self.args.baud} 8N1  ·  slave {self.args.slave}")
            self._update_overview(self._sections)
            self._append_history()
            self._update_tree(TAB_BMS, self._sections, {"АКБ через CAN / BMS"})
            self._update_tree(
                TAB_TELEMETRY,
                self._sections,
                {"PV, мережа, навантаження", "Пристрій і поточний стан"},
            )
            self._update_tree(TAB_SETTINGS, self._sections, SETTING_SECTIONS[TAB_SETTINGS])
            self.time_var.set(f"Оновлено: {datetime.now():%d.%m.%Y %H:%M:%S}")
            self._set_status("ok", "Підключено")
        except serial.SerialException as exc:
            self._set_status("error", "COM-порт")
            messagebox.showerror("COM-порт", f"Не вдалося відкрити {self.args.port}:\n{exc}")
        except core.ModbusRtuError as exc:
            self._set_status("error", "Modbus")
            messagebox.showerror("Modbus", str(exc))
        finally:
            self._refresh_job = self.after(REFRESH_MS, self.refresh)

    def _row_value(self, label: str) -> tuple[str, str]:
        for section in self._sections:
            for row in section["rows"]:
                if row["label"] == label:
                    unit = str(row.get("unit") or "")
                    return str(row["value"]), unit
        return "—", ""

    def _fmt(self, val: str, unit: str) -> str:
        text = f"{val} {unit}".strip()
        return text if text else "—"

    def _update_soc_bar(self, soc_text: str) -> None:
        soc = parse_number(soc_text)
        display = soc_text if soc_text != "—" else "—"
        self.soc_value.configure(text=display if "%" in display else f"{display} %")

        width = 0.0 if soc is None else max(0.0, min(soc, 100.0)) / 100.0
        self.soc_bar.place(relwidth=width)

        if soc is None:
            color = Theme.MUTED
        elif soc >= 60:
            color = Theme.BATTERY
        elif soc >= 20:
            color = Theme.WARN
        else:
            color = Theme.ERROR
        self.soc_bar.configure(bg=color)
        self.soc_value.configure(fg=color)

    def _update_overview(self, sections: list[core.Section]) -> None:
        self._sections = sections

        soc, _ = self._row_value("BMS SOC")
        self._update_soc_bar(soc)

        inv_v, _ = self._row_value("Напруга АКБ (інвертор)")
        inv_i, _ = self._row_value("Струм АКБ (інвертор)")
        inv_p, inv_u = self._row_value("Потужність АКБ (інвертор)")
        bms_v, _ = self._row_value("BMS напруга")
        bms_i, _ = self._row_value("BMS струм")
        self.hero_details.configure(
            text=f"Інвертор: {inv_v} В  ·  {inv_i} А  ·  {inv_p} {inv_u}\nCAN BMS: {bms_v} В  ·  {bms_i} А"
        )

        state, _ = self._row_value("Робочий стан")
        self.state_badge.configure(text=state)

        info = [
            self._row_value("Модель"),
            self._row_value("Прошивка"),
            self._row_value("Номінальна потужність"),
            self._row_value("Modbus-протокол"),
        ]
        for card, (val, unit) in zip(self._info_cards, info):
            card.set(self._fmt(val, unit))

        pv_p, pv_u = self._row_value("PV потужність")
        grid_p, grid_u = self._row_value("Потужність мережі")
        load_p, load_u = self._row_value("Потужність навантаження")
        batt_p = self._fmt(inv_p, inv_u)
        soc_disp = self._fmt(soc, "%")

        self.flow_canvas.update_values(
            self._fmt(pv_p, pv_u),
            self._fmt(grid_p, grid_u),
            self._fmt(load_p, load_u),
            batt_p,
            soc_disp,
        )

        mapping = {
            "pv_p": "PV потужність",
            "load_p": "Потужність навантаження",
            "grid_p": "Потужність мережі",
            "bms_v": "BMS напруга",
            "bms_i": "BMS струм",
            "bms_t": "BMS температура",
            "bat_type": "Тип акумулятора",
            "bms_soh": "BMS SOH",
        }
        for key, label in mapping.items():
            val, unit = self._row_value(label)
            self._overview_cards[key].set(self._fmt(val, unit))

        bms_map = {
            "bms_v": "BMS напруга",
            "bms_i": "BMS струм",
            "bms_soc": "BMS SOC",
            "bms_soh": "BMS SOH",
            "bms_t": "BMS температура",
            "bms_err": "BMS помилки",
        }
        for key, label in bms_map.items():
            val, unit = self._row_value(label)
            self._bms_cards[key].set(self._fmt(val, unit))

    def _update_tree(
        self,
        tab: str,
        sections: list[core.Section],
        titles: set[str],
    ) -> None:
        tree = self._trees[tab]
        for item in tree.get_children():
            tree.delete(item)

        for section in sections:
            if section["title"] not in titles:
                continue
            parent = tree.insert(
                "",
                "end",
                values=("", f"  ▸  {section['title']}", "", "", ""),
                tags=("section",),
            )
            for idx, row in enumerate(section["rows"]):
                prog = row.get("program")
                reg = row.get("register")
                tag = "odd" if idx % 2 else "even"
                tree.insert(
                    parent,
                    "end",
                    values=(
                        f"[{prog:02d}]" if prog is not None else "",
                        row["label"],
                        row["value"],
                        row.get("unit") or "",
                        reg if reg is not None else "",
                    ),
                    tags=(tag,),
                )
            tree.item(parent, open=True)


def run_gui(args: argparse.Namespace) -> None:
    app = MustMonitorApp(args)
    app.mainloop()
