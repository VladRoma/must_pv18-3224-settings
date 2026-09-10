const REFRESH_MS = 5000;
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

  const minutes = Math.max(1, Math.round((history.labels.length * REFRESH_MS) / 60000));
  document.getElementById("chart-info").textContent =
    `Точок: ${history.labels.length} · ~${minutes} хв історії · макс. ${HISTORY_MAX}`;
}

function updateOverview(payload) {
  const m = payload.metrics;
  const n = payload.numbers || {};
  const socText = fmtMetric(m.soc);
  const socValue = document.getElementById("soc-value");
  const socFill = document.getElementById("soc-fill");

  socValue.textContent = socText;
  socValue.style.color = socColor(n.soc);
  socFill.style.width = n.soc == null ? "0%" : `${Math.max(0, Math.min(n.soc, 100))}%`;
  socFill.style.background = socColor(n.soc);

  document.getElementById("hero-meta").textContent =
    `Інвертор: ${fmtMetric(m.batt_v)} · ${fmtMetric(m.batt_i)} · ${fmtMetric(m.batt_p)}\n` +
    `CAN BMS: ${fmtMetric(m.bms_v)} · ${fmtMetric(m.bms_i)}`;
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
  renderCards("bms-cards", BMS_CARDS.map(([t, k, c]) => [t, k, c, "CAN"]), m, true);

  renderTable("table-bms", payload.sections, new Set(["АКБ через CAN / BMS"]));
  renderTable(
    "table-energy",
    payload.sections,
    new Set(["PV, мережа, навантаження", "Пристрій і поточний стан"])
  );
  renderTable("table-settings", payload.sections, SETTING_SECTIONS);
}

async function refresh() {
  if (refreshTimer) {
    clearTimeout(refreshTimer);
    refreshTimer = null;
  }

  setStatus("loading", "Читання…");
  try {
    const res = await fetch("/api/data", { cache: "no-store" });
    const payload = await res.json();
    if (!payload.ok) {
      throw new Error(payload.message || "Помилка зчитування");
    }

    const conn = payload.connection;
    document.getElementById("conn-line").textContent =
      `${conn.port} · ${conn.baud} 8N1 · slave ${conn.slave}`;
    document.getElementById("updated-at").textContent =
      `Оновлено: ${new Date(payload.updated).toLocaleString("uk-UA")}`;

    updateOverview(payload);
    pushHistory(payload);
    updateCharts();
    setStatus("ok", "Підключено");
  } catch (err) {
    setStatus("error", "Помилка");
    console.error(err);
  } finally {
    refreshTimer = setTimeout(refresh, REFRESH_MS);
  }
}

function setupTabs() {
  document.querySelectorAll(".tab").forEach((tab) => {
    tab.addEventListener("click", () => {
      document.querySelectorAll(".tab").forEach((el) => el.classList.remove("active"));
      document.querySelectorAll(".panel").forEach((el) => el.classList.remove("active"));
      tab.classList.add("active");
      document.getElementById(`panel-${tab.dataset.tab}`).classList.add("active");
    });
  });
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
setupTabs();
setupChartControls();
initCharts();
refresh();
