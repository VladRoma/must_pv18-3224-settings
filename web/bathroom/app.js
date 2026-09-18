const API = "/api/bathroom/data";
const POLL_MS = 5000;

const el = (id) => document.getElementById(id);

let chart;

function tempBand(c) {
  if (c == null || Number.isNaN(c)) return "—";
  if (c < 20) return "прохолодно";
  if (c <= 26) return "комфортно";
  return "тепло";
}

function humBand(p) {
  if (p == null || Number.isNaN(p)) return "—";
  if (p < 40) return "низька";
  if (p <= 65) return "норма";
  if (p <= 75) return "підвищена";
  return "висока";
}

function setGauge(ringId, valueId, value, min, max) {
  const ring = el(ringId);
  const pct = value == null ? 0 : Math.max(0, Math.min(100, ((value - min) / (max - min)) * 100));
  ring.style.setProperty("--pct", String(pct));
  el(valueId).textContent = value == null ? "—" : String(value);
}

function applyComfort(code, title, hint) {
  const hero = document.querySelector(".hero");
  hero.classList.remove("ok", "warn", "critical", "dry", "hot", "cold");
  if (code) hero.classList.add(code);
  el("comfort-title").textContent = title || "—";
  el("comfort-hint").textContent = hint || "";
}

function formatLocal(iso) {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString("uk-UA", { dateStyle: "short", timeStyle: "medium" });
  } catch {
    return iso;
  }
}

function updateChart(history) {
  const labels = (history || []).map((p) => {
    const d = new Date(p.at);
    return d.toLocaleTimeString("uk-UA", { hour: "2-digit", minute: "2-digit" });
  });
  const temps = (history || []).map((p) => p.temperature_c);
  const hums = (history || []).map((p) => p.humidity_pct);

  if (!chart) {
    const ctx = el("history-chart").getContext("2d");
    chart = new Chart(ctx, {
      type: "line",
      data: {
        labels,
        datasets: [
          {
            label: "°C",
            data: temps,
            borderColor: "#ffb86b",
            backgroundColor: "rgba(255, 184, 107, 0.12)",
            tension: 0.35,
            yAxisID: "y",
          },
          {
            label: "%",
            data: hums,
            borderColor: "#5eead4",
            backgroundColor: "rgba(94, 234, 212, 0.1)",
            tension: 0.35,
            yAxisID: "y1",
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        interaction: { mode: "index", intersect: false },
        plugins: { legend: { labels: { color: "#8aa8c4" } } },
        scales: {
          x: { ticks: { color: "#8aa8c4", maxRotation: 0 }, grid: { color: "rgba(255,255,255,0.06)" } },
          y: {
            position: "left",
            suggestedMin: 10,
            suggestedMax: 35,
            ticks: { color: "#ffb86b" },
            grid: { color: "rgba(255,255,255,0.06)" },
          },
          y1: {
            position: "right",
            min: 0,
            max: 100,
            ticks: { color: "#5eead4" },
            grid: { drawOnChartArea: false },
          },
        },
      },
    });
    return;
  }

  chart.data.labels = labels;
  chart.data.datasets[0].data = temps;
  chart.data.datasets[1].data = hums;
  chart.update("none");
}

async function refresh() {
  try {
    const res = await fetch(API, { cache: "no-store" });
    const data = await res.json();
    const r = data.reading;
    const pill = el("link-pill");

    if (data.online) {
      pill.textContent = "● онлайн";
      pill.className = "pill online";
    } else {
      pill.textContent = "● офлайн";
      pill.className = "pill offline";
    }

    if (!r) {
      applyComfort(null, "Очікування даних", "ESP32 ще не надсилав показники на Raspberry Pi.");
      setGauge("ring-temp", "temp-value", null, 10, 35);
      setGauge("ring-hum", "hum-value", null, 0, 100);
      el("temp-meta").textContent = "—";
      el("hum-meta").textContent = "—";
      el("dew-value").textContent = "— °C";
      el("updated-at").textContent = "—";
      el("device-name").textContent = "—";
      return;
    }

    setGauge("ring-temp", "temp-value", r.temperature_c, 10, 35);
    setGauge("ring-hum", "hum-value", r.humidity_pct, 0, 100);
    el("temp-meta").textContent = tempBand(r.temperature_c);
    el("hum-meta").textContent = humBand(r.humidity_pct);
    el("dew-value").textContent = `${r.dew_point_c ?? "—"} °C`;
    el("updated-at").textContent = formatLocal(data.updated);
    el("device-name").textContent = r.device || "esp32";

    const c = data.comfort || {};
    applyComfort(c.code, c.title, c.hint);
    updateChart(data.history || []);
  } catch (err) {
    console.error(err);
    el("link-pill").textContent = "● помилка";
    el("link-pill").className = "pill offline";
  }
}

refresh();
setInterval(refresh, POLL_MS);
