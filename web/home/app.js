const API = "/api/home/summary";
const POLL_MS = 15000;

const el = (id) => document.getElementById(id);

function ventBadge(code, ventilate) {
  if (code === "offline") return "немає даних";
  if (ventilate === true) return "ТАК";
  if (ventilate === false) return "НІ";
  return "—";
}

function applyVentilation(v) {
  const card = el("vent-card");
  card.classList.remove("ok", "watch", "soon", "now", "offline");
  if (v && v.code) card.classList.add(v.code);

  el("vent-title").textContent = v?.title || "—";
  el("vent-detail").textContent = v?.detail || "—";
  el("vent-badge").textContent = ventBadge(v?.code, v?.ventilate);
}

function applyMust(m) {
  el("must-state").textContent = m?.state || "—";
  el("must-soc").textContent = m?.soc || "—";
  el("must-pv").textContent = m?.pv || "—";
  el("must-load").textContent = m?.load || "—";
  el("must-batt").textContent = m?.batt || "—";
  el("must-bms").textContent = m?.bms_err || "—";
  if (m?.error) {
    el("must-state").textContent = "Немає зв’язку";
  }
}

function applyBattery(b) {
  if (!b) {
    el("bat-state").textContent = "—";
    el("bat-soc").textContent = "—";
    el("bat-power").textContent = "—";
    el("bat-v").textContent = "—";
    el("bat-i").textContent = "—";
    return;
  }
  el("bat-state").textContent = b.state || "—";
  el("bat-soc").textContent = b.soc != null ? `${b.soc}%` : "—";
  el("bat-power").textContent = b.power != null ? `${b.power} Вт` : "—";
  el("bat-v").textContent = b.voltage != null ? `${b.voltage.toFixed(1)} В` : "—";
  el("bat-i").textContent = b.current != null ? `${b.current.toFixed(1)} А` : "—";
}

function applyPiPower(p) {
  const card = document.querySelector(".pi-power");
  const label = el("pi-ext5v-label");
  const value = el("pi-ext5v-value");
  const hint = el("pi-ext5v-hint");

  card?.classList.remove("ok", "warn");

  if (!p?.ok || p.volts == null) {
    label.textContent = p?.label || "Вхідна напруга";
    value.textContent = "—";
    hint.textContent =
      p?.error || "Доступно на Raspberry Pi 5 (vcgencmd pmic_read_adc).";
    return;
  }

  label.textContent = p.label || "Вхідна напруга";
  value.textContent = p.volts.toFixed(2);
  hint.textContent = p.hint || "—";

  if (p.volts >= 4.75 && p.volts <= 5.25) {
    card?.classList.add("ok");
  } else {
    card?.classList.add("warn");
  }
}

function applyBathroom(b) {
  if (!b?.online || b.temperature_c == null) {
    el("bath-main").textContent = "Офлайн";
    el("bath-t").textContent = "—";
    el("bath-h").textContent = "—";
    el("bath-dew").textContent = "—";
    el("bath-status").textContent = "—";
    return;
  }
  el("bath-main").textContent = `${b.temperature_c} °C · ${b.humidity_pct}%`;
  el("bath-t").textContent = `${b.temperature_c} °C`;
  el("bath-h").textContent = `${b.humidity_pct} %`;
  el("bath-dew").textContent = b.dew_point_c != null ? `${b.dew_point_c} °C` : "—";
  el("bath-status").textContent = b.comfort_title || "—";
}

function formatLocal(iso) {
  if (!iso) return "—";
  try {
    return new Date(iso).toLocaleString("uk-UA", { dateStyle: "short", timeStyle: "medium" });
  } catch {
    return iso;
  }
}

async function refresh() {
  const pill = el("status-pill");
  try {
    const res = await fetch(API, { cache: "no-store" });
    const data = await res.json();
    if (!data.ok) throw new Error("bad response");

    applyVentilation(data.ventilation);
    applyMust(data.must);
    applyBattery(data.must?.battery);
    applyBathroom(data.bathroom);
    applyPiPower(data.pi_power);
    el("updated-line").textContent = `Оновлено: ${formatLocal(data.updated)} · наступне через ${POLL_MS / 1000} с`;

    pill.textContent = "● live";
    pill.className = "pill live";
  } catch (err) {
    console.error(err);
    pill.textContent = "● помилка";
    pill.className = "pill err";
  }
}

refresh();
setInterval(refresh, POLL_MS);
