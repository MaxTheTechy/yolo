function api(path) {
  return path + (path.includes("?") ? "&" : "?") + "room=" + window.ROOM_ID;
}

async function refreshCurrent() {
  const res = await fetch(api("/api/current"));
  const data = await res.json();
  document.getElementById("people-count").textContent = data.people_count;
  document.getElementById("occupancy-percent").textContent = data.occupancy_percent + "%";
  document.getElementById("last-updated").textContent = data.timestamp
    ? new Date(data.timestamp).toLocaleTimeString()
    : "-";

  const img = document.getElementById("latest-snapshot");
  const caption = document.getElementById("latest-snapshot-caption");
  if (data.snapshot_url) {
    img.src = data.snapshot_url;
    img.style.display = "block";
    caption.textContent = `${data.people_count} people - ${new Date(data.timestamp).toLocaleString()}`;
  } else {
    img.removeAttribute("src");
    img.style.display = "none";
    caption.textContent = "No recent snapshot";
  }
}

async function refreshHistory() {
  const res = await fetch(api("/api/history"));
  const data = await res.json();
  document.getElementById("went-in-24h").textContent = data.went_in;
  document.getElementById("went-out-24h").textContent = data.went_out;
  const tbody = document.querySelector("#history-table tbody");
  tbody.innerHTML = "";
  data.samples.slice().reverse().forEach((r) => {
    const tr = document.createElement("tr");
    const peopleCell = r.snapshot_url
      ? `<a href="${r.snapshot_url}" target="_blank" rel="noopener">${r.people_count}</a>`
      : r.people_count;
    tr.innerHTML = `<td>${new Date(r.timestamp).toLocaleString()}</td><td>${peopleCell}</td><td>${r.occupancy_percent}%</td>` +
      `<td>${r.went_in || ""}</td><td>${r.went_out || ""}</td>`;
    tbody.appendChild(tr);
  });
}

function formatPeak(p) {
  if (!p) return "-";
  return `${p.people_count} people (${new Date(p.timestamp).toLocaleString()})`;
}

async function refreshPeak() {
  const res = await fetch(api("/api/stats/peak"));
  const data = await res.json();
  document.getElementById("peak-today").textContent = formatPeak(data.today);
  document.getElementById("peak-all-time").textContent = formatPeak(data.all_time);
}

function renderHourlyChart(rows) {
  const svg = document.getElementById("hourly-chart");
  const width = 280;
  const height = 220;
  const padding = 22;
  const plotWidth = width - padding * 2;
  const plotHeight = height - padding * 2;
  const barGap = 2;
  const barWidth = plotWidth / 24 - barGap;
  const byHour = {};
  rows.forEach((r) => { byHour[r.hour] = r.max_people; });
  const maxVal = Math.max(window.ROOM_CAPACITY || 1, ...Object.values(byHour));

  let svgContent = `<line x1="${padding}" y1="${height - padding}" x2="${width - padding}" y2="${height - padding}" stroke="#ccc" />`;

  for (let h = 0; h < 24; h++) {
    const val = byHour[h] || 0;
    const barHeight = (Math.min(val, maxVal) / maxVal) * plotHeight;
    const x = padding + h * (plotWidth / 24) + barGap / 2;
    const y = height - padding - barHeight;
    svgContent += `<rect x="${x.toFixed(1)}" y="${y.toFixed(1)}" width="${barWidth.toFixed(1)}" height="${barHeight.toFixed(1)}" fill="#3b82f6"><title>${String(h).padStart(2, "0")}:00 - max ${val} inside</title></rect>`;
  }

  for (let h = 0; h < 24; h += 4) {
    const x = padding + h * (plotWidth / 24);
    svgContent += `<text x="${x.toFixed(1)}" y="${height - padding + 12}" font-size="9" fill="#666">${h}</text>`;
  }

  svg.innerHTML = svgContent;
}

async function refreshHourly() {
  const res = await fetch(api("/api/stats/hourly"));
  const rows = await res.json();
  const tbody = document.querySelector("#hourly-table tbody");
  tbody.innerHTML = "";
  rows.forEach((r) => {
    const tr = document.createElement("tr");
    const hourLabel = String(r.hour).padStart(2, "0") + ":00";
    tr.innerHTML = `<td>${hourLabel}</td><td>${r.avg_people}</td><td>${r.max_people}</td>`;
    tbody.appendChild(tr);
  });
  renderHourlyChart(rows);
}

async function refreshDaily() {
  const res = await fetch(api("/api/stats/daily?days=7"));
  const rows = await res.json();
  const tbody = document.querySelector("#daily-table tbody");
  tbody.innerHTML = "";
  rows.slice().reverse().forEach((r) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `<td>${r.day}</td><td>${r.avg_people}</td><td>${r.max_people}</td><td>${r.peak_occupancy}%</td><td>${r.samples}</td>`;
    tbody.appendChild(tr);
  });
}

function refreshAll() {
  refreshCurrent();
  refreshHistory();
  refreshPeak();
  refreshHourly();
  refreshDaily();
}

refreshAll();
setInterval(refreshAll, 10000);
