const RETRY_MS = 5000;
const CONNECTED_REFRESH_MS = 5 * 60 * 1000;
const HISTORY_MAX = 720;

const SETTING_SECTIONS = new Set([
  "Пріоритети та мережа",
  "Заряджання",
  "Напруги акумулятора (USE / LI)",
  "Дисплей і сигналізація",
  "Вирівнювання (Equalization)",
  "BMS / літій (LI)",
]);

const INFO_CARDS = [
  ["Модель", "model", "var(--info)", "пристрій"],
  ["Прошивка", "firmware", "var(--muted)", "версія"],
  ["Потужність", "nominal_power", "var(--accent)", "номінал"],
  ["Протокол", "protocol", "var(--grid)", "Modbus"],
];

const METRIC_CARDS = [
  ["PV", "pv_p", "var(--solar)", "сонячна генерація"],
  ["Навантаження", "load_p", "var(--load)", "споживання"],
  ["Мережа", "grid_p", "var(--grid)", "обмін з мережею"],
  ["BMS U", "bms_v", "var(--battery)", "напруга CAN"],
  ["BMS I", "bms_i", "var(--battery)", "струм CAN"],
  ["BMS T", "bms_t", "var(--info)", "температура"],
  ["Тип АКБ", "bat_type", "var(--accent)", "програма 14"],
  ["BMS SOH", "soh", "var(--battery)", "стан здоров'я"],
];

const BMS_CARDS = [
  ["Напруга BMS", "bms_v", "var(--battery)"],
  ["Струм BMS", "bms_i", "var(--battery)"],
  ["SOC", "soc", "var(--ok)"],
  ["SOH", "soh", "var(--info)"],
  ["Температура", "bms_t", "var(--warn)"],
  ["Помилки", "bms_err", "var(--error)"],
];

const BMS_WIFI_CARDS = [
  ["Напруга пакета", "bms_v", "var(--battery)"],
  ["Струм", "bms_i", "var(--battery)"],
  ["Потужність", "wifi_p", "var(--battery)"],
  ["SOC", "soc", "var(--ok)"],
  ["SOH", "soh", "var(--info)"],
  ["Стан", "wifi_state", "var(--accent)"],
  ["Залишок", "remain_ah", "var(--info)"],
  ["Цикли", "cycles", "var(--muted)"],
];

const history = {
  labels: [],
  soc: [],
  pv: [],
  load: [],
  grid: [],
  batt: [],
};

let refreshTimer = null;
let socChart = null;
let powerChart = null;
let latestWritable = [];
let toastTimer = null;
let isConnected = false;
let activeTab = "overview";
let settingsAuthed = false;

function isChartTabActive() {
  return activeTab === "chart";
}

function getRefreshDelay() {
  if (!isConnected) return RETRY_MS;
  if (isChartTabActive()) return RETRY_MS;
  return CONNECTED_REFRESH_MS;
}

function fmtMetric(metric) {
  if (!metric) return "—";
  const unit = metric.unit ? ` ${metric.unit}` : "";
  return `${metric.value}${unit}`.trim() || "—";
}

function setStatus(state, text) {
  const pill = document.getElementById("status-pill");
  pill.className = "pill";
  if (state === "ok") pill.classList.add("ok");
  if (state === "loading") pill.classList.add("loading");
  if (state === "error") pill.classList.add("error");
  pill.textContent = `● ${text}`;
}

function socColor(value) {
  if (value == null) return "var(--muted)";
  if (value >= 60) return "var(--battery)";
  if (value >= 20) return "var(--warn)";
  return "var(--error)";
}

function flowStrength(watts) {
  if (watts == null) return 2;
  return Math.min(6, 2 + Math.abs(watts) / 800);
}

function updateFlowLine(id, watts, color, reverse = false) {
  const line = document.getElementById(id);
  if (!line) return;
  line.setAttribute("stroke", watts == null || Math.abs(watts) < 5 ? "#334155" : color);
  line.setAttribute("stroke-width", flowStrength(watts));
  if (reverse) {
    line.setAttribute("marker-start", "url(#arrow)");
    line.setAttribute("marker-end", "none");
  } else {
    line.removeAttribute("marker-start");
    line.setAttribute("marker-end", "url(#arrow)");
  }
}

function renderCards(containerId, cards, metrics, large = false) {
  const root = document.getElementById(containerId);
  root.innerHTML = cards
    .map(([title, key, color, subtitle]) => {
      const value = fmtMetric(metrics[key]);
      return `
        <article class="card">
          <div class="card-stripe" style="background:${color}"></div>
          <div class="card-body">
            <div class="metric-title">${title}</div>
            <div class="metric-value" style="color:${color}; font-size:${large ? "1.7rem" : "1.45rem"}">${value}</div>
            ${subtitle ? `<div class="metric-sub">${subtitle}</div>` : ""}
          </div>
        </article>`;
    })
    .join("");
}

function renderTable(containerId, sections, titles) {
  const root = document.getElementById(containerId);
  const rows = [];

  for (const section of sections) {
    if (!titles.has(section.title)) continue;
    rows.push(`<tr class="section-row"><td colspan="5">▸ ${section.title}</td></tr>`);
    for (const row of section.rows) {
      const prog = row.program != null ? `[${String(row.program).padStart(2, "0")}]` : "";
      const reg = row.register != null ? row.register : "";
      rows.push(`
        <tr>
          <td>${prog}</td>
          <td>${row.label}</td>
          <td>${row.value}</td>
          <td>${row.unit || ""}</td>
          <td>${reg}</td>
        </tr>`);
    }
  }

  root.innerHTML = `
    <table>
      <thead>
        <tr>
          <th>Пр.</th>
          <th>Параметр</th>
          <th>Значення</th>
          <th>Од.</th>
          <th>Reg</th>
        </tr>
      </thead>
      <tbody>${rows.join("")}</tbody>
    </table>`;
}

function pushHistory(payload) {
  const n = payload.numbers || {};
  const label = new Date().toLocaleTimeString("uk-UA", { hour: "2-digit", minute: "2-digit", second: "2-digit" });
  history.labels.push(label);
  history.soc.push(n.soc);
  history.pv.push(n.pv_w);
  history.load.push(n.load_w);
  history.grid.push(n.grid_w);
  history.batt.push(n.batt_w);

  for (const key of Object.keys(history)) {
    if (history[key].length > HISTORY_MAX) history[key].shift();
  }
}

function chartOptions(yMin, yMax, unit) {
  return {
    responsive: true,
    maintainAspectRatio: false,
    animation: { duration: 250 },
    scales: {
      x: {
        ticks: { color: "#7d92ad", maxTicksLimit: 8 },
        grid: { color: "#243044" },
      },
      y: {
        min: yMin,
        max: yMax,
        ticks: { color: "#7d92ad" },
        grid: { color: "#243044" },
        title: unit ? { display: true, text: unit, color: "#7d92ad" } : undefined,
      },
    },
    plugins: {
      legend: {
        labels: { color: "#eef2f7" },
      },
    },
  };
}

function initCharts() {
  const socCtx = document.getElementById("soc-chart");
  const powerCtx = document.getElementById("power-chart");

  socChart = new Chart(socCtx, {
    type: "line",
    data: {
      labels: history.labels,
      datasets: [{
        label: "SOC",
        data: history.soc,
        borderColor: "#34d399",
        backgroundColor: "rgba(52, 211, 153, 0.15)",
        fill: true,
        tension: 0.3,
        pointRadius: 0,
      }],
    },
    options: chartOptions(0, 100, "%"),
  });

  powerChart = new Chart(powerCtx, {
    type: "line",
    data: {
      labels: history.labels,
      datasets: [
        { label: "PV", key: "pv", data: history.pv, borderColor: "#fcd34d", tension: 0.3, pointRadius: 0 },
        { label: "Load", key: "load", data: history.load, borderColor: "#f472b6", tension: 0.3, pointRadius: 0 },
        { label: "Grid", key: "grid", data: history.grid, borderColor: "#60a5fa", tension: 0.3, pointRadius: 0 },
        { label: "АКБ", key: "batt", data: history.batt, borderColor: "#34d399", tension: 0.3, pointRadius: 0 },
      ],
    },
    options: chartOptions(undefined, undefined, "Вт"),
  });
}

function updateCharts() {
  socChart.data.labels = history.labels;
  socChart.data.datasets[0].data = history.soc;
  socChart.update();

  powerChart.data.labels = history.labels;
  for (const ds of powerChart.data.datasets) {
    ds.data = history[ds.key];
    ds.hidden = !document.querySelector(`input[data-series="${ds.key}"]`)?.checked;
  }
  powerChart.update();

  const interval = getRefreshDelay();
  const minutes = Math.max(1, Math.round((history.labels.length * interval) / 60000));
  document.getElementById("chart-info").textContent =
    `Точок: ${history.labels.length} · ~${minutes} хв історії · макс. ${HISTORY_MAX}`;
}

function scheduleRefresh() {
  refreshTimer = setTimeout(refresh, getRefreshDelay());
}

function resetRefreshTimer() {
  if (refreshTimer) {
    clearTimeout(refreshTimer);
    refreshTimer = null;
  }
  updateRefreshHint();
  scheduleRefresh();
}

function updateRefreshHint() {
  const el = document.getElementById("refresh-hint");
  if (!el) return;
  if (!isConnected) {
    el.textContent = "Повтор підключення: кожні 5 с";
  } else if (isChartTabActive()) {
    el.textContent = "Графік: оновлення кожні 5 с · або кнопка ↻";
  } else {
    el.textContent = "Автооновлення: кожні 5 хв · або кнопка ↻";
  }
}

function renderCells(battery) {
  const root = document.getElementById("cell-grid");
  if (!root) return;
  if (!battery || !battery.ok || !battery.cells || !battery.cells.length) {
    root.innerHTML = "";
    return;
  }
  const min = 2.8;
  const max = 3.65;
  root.innerHTML = battery.cells
    .map((voltage, index) => {
      const pct = Math.max(0, Math.min(100, ((voltage - min) / (max - min)) * 100));
      const warn = voltage < 3.0 || voltage > 3.55;
      const color = warn ? "var(--error)" : "var(--battery)";
      return `
        <article class="cell-card">
          <div class="cell-label">Комірка ${String(index + 1).padStart(2, "0")}</div>
          <div class="cell-value" style="color:${color}">${voltage.toFixed(3)} В</div>
          <div class="cell-bar"><span style="width:${pct}%; background:${color}"></span></div>
        </article>`;
    })
    .join("");
}

function updateOverview(payload) {
  const m = payload.metrics;
  const n = payload.numbers || {};
  const battery = payload.battery;
  const wifiOk = Boolean(battery && battery.ok);
  const socText = fmtMetric(m.soc);
  const socValue = document.getElementById("soc-value");
  const socFill = document.getElementById("soc-fill");

  socValue.textContent = socText;
  socValue.style.color = socColor(n.soc);
  socFill.style.width = n.soc == null ? "0%" : `${Math.max(0, Math.min(n.soc, 100))}%`;
  socFill.style.background = socColor(n.soc);

  const wifiLine = wifiOk
    ? `Wi‑Fi BMS: ${battery.voltage.toFixed(1)} В · ${battery.current.toFixed(1)} А · ${battery.power.toFixed(0)} Вт · ${battery.state}`
    : battery && battery.error
      ? `Wi‑Fi BMS: ${battery.error}`
      : "Wi‑Fi BMS: не налаштовано";
  document.getElementById("hero-meta").textContent =
    `Інвертор: ${fmtMetric(m.batt_v)} · ${fmtMetric(m.batt_i)} · ${fmtMetric(m.batt_p)}\n` +
    wifiLine;
  document.getElementById("state-badge").textContent = fmtMetric(m.state);

  document.getElementById("flow-pv").textContent = fmtMetric(m.pv_p);
  document.getElementById("flow-grid").textContent = fmtMetric(m.grid_p);
  document.getElementById("flow-load").textContent = fmtMetric(m.load_p);
  document.getElementById("flow-batt").textContent = fmtMetric(m.batt_p);
  document.getElementById("flow-soc").textContent = fmtMetric(m.soc);

  updateFlowLine("line-pv", n.pv_w, "#fcd34d");
  updateFlowLine("line-grid", n.grid_w, "#60a5fa", (n.grid_w || 0) < 0);
  updateFlowLine("line-load", n.load_w, "#f472b6");

  renderCards("info-cards", INFO_CARDS, m);
  renderCards("metric-cards", METRIC_CARDS, m);
  const bmsCards = wifiOk ? BMS_WIFI_CARDS : BMS_CARDS;
  const bmsSource = wifiOk ? "Wi‑Fi PACEEX" : "CAN";
  renderCards("bms-cards", bmsCards.map(([t, k, c]) => [t, k, c, bmsSource]), m, true);

  const banner = document.getElementById("bms-banner");
  if (banner) {
    if (wifiOk) {
      const delta = battery.cell_delta != null ? ` · Δ ${battery.cell_delta.toFixed(3)} В` : "";
      const serial = battery.serial ? ` · S/N ${battery.serial}` : "";
      banner.textContent =
        `LP16-24200 · ${battery.host}:${battery.port} · ${battery.cell_count} комірок${delta}${serial}`;
    } else {
      banner.textContent = "CAN → інвертор → Modbus · регістри 109–114";
    }
  }
  renderCells(battery);

  renderTable("table-bms", payload.sections, new Set(["АКБ через CAN / BMS"]));
  renderTable(
    "table-energy",
    payload.sections,
    new Set(["PV, мережа, навантаження", "Пристрій і поточний стан"])
  );
  latestWritable = payload.writable || [];
  settingsAuthed = Boolean(payload.settings_auth);
  renderSettingsPanel();
}

function showSettingsGate(show) {
  document.getElementById("settings-gate").classList.toggle("hidden", !show);
  document.getElementById("settings-unlocked").classList.toggle("hidden", show);
}

function renderSettingsPanel() {
  if (!settingsAuthed) {
    showSettingsGate(true);
    document.getElementById("settings-editor").innerHTML = "";
    return;
  }
  showSettingsGate(false);
  renderSettingsEditor(latestWritable);
}

function showToast(text, ok = true) {
  const toast = document.getElementById("settings-toast");
  toast.textContent = text;
  toast.className = ok ? "toast ok" : "toast error";
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.add("hidden"), 4000);
}

function inputForSetting(item) {
  if (item.options && item.options.length) {
    const select = document.createElement("select");
    for (const option of item.options) {
      const opt = document.createElement("option");
      opt.value = String(option.label);
      opt.textContent = option.label;
      if (String(option.label) === String(item.value_display)) opt.selected = true;
      select.appendChild(opt);
    }
    return select;
  }
  const input = document.createElement("input");
  input.type = "number";
  input.step = item.kind === "frequency" ? "0.01" : item.kind === "integer" || item.kind === "percent" ? "1" : "0.1";
  input.value = String(item.value_display).replace(/[^\d.,-]/g, "") || "";
  return input;
}

function renderSettingsEditor(items) {
  const root = document.getElementById("settings-editor");
  if (!items.length) {
    root.innerHTML = `<div class="card banner">Налаштування для запису недоступні</div>`;
    return;
  }

  const bySection = new Map();
  for (const item of items) {
    if (!bySection.has(item.section)) bySection.set(item.section, []);
    bySection.get(item.section).push(item);
  }

  root.innerHTML = "";
  for (const [section, rows] of bySection.entries()) {
    const block = document.createElement("section");
    block.className = "settings-section";
    block.innerHTML = `<h3>${section}</h3>`;

    const wrap = document.createElement("div");
    wrap.className = "settings-wrap";

    const head = document.createElement("div");
    head.className = "setting-row head";
    head.innerHTML = "<div>Пр.</div><div>Параметр</div><div>Зараз</div><div>Нове значення</div><div></div>";
    wrap.appendChild(head);

    for (const item of rows) {
      const row = document.createElement("div");
      row.className = "setting-row";
      row.innerHTML = `
        <div>[${String(item.program).padStart(2, "0")}]</div>
        <div>${item.label}</div>
        <div class="current">${item.value_display}${item.unit ? ` ${item.unit}` : ""}</div>
      `;

      const inputWrap = document.createElement("div");
      const input = inputForSetting(item);
      inputWrap.appendChild(input);

      const btnWrap = document.createElement("div");
      const btn = document.createElement("button");
      btn.type = "button";
      btn.textContent = "Зберегти";
      btn.addEventListener("click", () => saveSetting(item, input, btn));
      btnWrap.appendChild(btn);

      row.appendChild(inputWrap);
      row.appendChild(btnWrap);
      wrap.appendChild(row);
    }

    block.appendChild(wrap);
    root.appendChild(block);
  }
}

async function saveSetting(item, input, button) {
  const value = input.value;
  if (!value && value !== "0") {
    showToast("Введи значення", false);
    return;
  }
  if (!confirm(`Записати [${String(item.program).padStart(2, "0")}] ${item.label} = ${value}?`)) {
    return;
  }

  button.disabled = true;
  try {
    const res = await fetch("/api/settings/write", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ program: item.program, value }),
    });
    const payload = await res.json();
    if (res.status === 401) {
      settingsAuthed = false;
      renderSettingsPanel();
      throw new Error(payload.message || "Потрібен пароль");
    }
    if (!payload.ok) {
      throw new Error(payload.message || "Помилка запису");
    }
    showToast(`Записано [${String(item.program).padStart(2, "0")}] ${item.label}`);
    await refresh();
  } catch (err) {
    showToast(err.message || "Помилка запису", false);
  } finally {
    button.disabled = false;
  }
}

async function refresh() {
  if (refreshTimer) {
    clearTimeout(refreshTimer);
    refreshTimer = null;
  }

  setStatus("loading", "Читання…");
  try {
    const res = await fetch("/api/data", { cache: "no-store", credentials: "same-origin" });
    const payload = await res.json();
    if (!payload.ok) {
      throw new Error(payload.message || "Помилка зчитування");
    }

    const conn = payload.connection;
    const parts = [];
    if (!conn.bms_only) {
      parts.push(`${conn.port} · ${conn.baud} 8N1 · slave ${conn.slave}`);
    }
    if (conn.bms_host) {
      parts.push(`АКБ Wi‑Fi ${conn.bms_host}:${conn.bms_port}`);
    }
    document.getElementById("conn-line").textContent = parts.join("  ·  ") || "—";
    document.getElementById("updated-at").textContent =
      `Оновлено: ${new Date(payload.updated).toLocaleString("uk-UA")}`;

    updateOverview(payload);
    pushHistory(payload);
    updateCharts();
    isConnected = true;
    setStatus("ok", "Підключено");
  } catch (err) {
    isConnected = false;
    setStatus("error", "Немає з'єднання");
    console.error(err);
  } finally {
    updateRefreshHint();
    scheduleRefresh();
  }
}

function setupTabs() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((el) => el.classList.remove("active"));
      document.querySelectorAll(".panel").forEach((el) => el.classList.remove("active"));
      tab.classList.add("active");
      const tabId = tab.dataset.tab;
      document.getElementById(`panel-${tabId}`).classList.add("active");
      activeTab = tabId;
      if (tabId === "settings") renderSettingsPanel();
      resetRefreshTimer();
    });
  });
}

async function loginSettings(event) {
  event.preventDefault();
  const input = document.getElementById("settings-password");
  const error = document.getElementById("settings-login-error");
  error.classList.add("hidden");
  try {
    const res = await fetch("/api/auth/login", {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ password: input.value }),
    });
    const payload = await res.json();
    if (!payload.ok) {
      throw new Error(payload.message || "Невірний пароль");
    }
    input.value = "";
    settingsAuthed = true;
    await refresh();
    renderSettingsPanel();
  } catch (err) {
    error.textContent = err.message || "Невірний пароль";
    error.classList.remove("hidden");
  }
}

async function logoutSettings() {
  await fetch("/api/auth/logout", { method: "POST", credentials: "same-origin" });
  settingsAuthed = false;
  latestWritable = [];
  renderSettingsPanel();
}

function setupChartControls() {
  document.querySelectorAll(".chart-toggles input").forEach((input) => {
    input.addEventListener("change", updateCharts);
  });

  document.getElementById("clear-chart").addEventListener("click", () => {
    for (const key of Object.keys(history)) history[key] = [];
    updateCharts();
  });
}

document.getElementById("refresh-btn").addEventListener("click", refresh);
document.getElementById("settings-login").addEventListener("submit", loginSettings);
document.getElementById("settings-logout").addEventListener("click", logoutSettings);
setupTabs();
setupChartControls();
initCharts();
refresh();
