let state = { rooms: [], cameras: [] };
let zoneCamera = null;
let zonePoints = [];

const msg = document.getElementById("admin-msg");
const roomForm = document.getElementById("room-form");
const cameraForm = document.getElementById("camera-form");

async function call(method, url, body) {
  const res = await fetch(url, {
    method,
    headers: { "Content-Type": "application/json" },
    body: body ? JSON.stringify(body) : undefined,
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}

function cell(tr, text) {
  const td = document.createElement("td");
  td.textContent = text;
  tr.appendChild(td);
  return td;
}

function button(td, label, onClick) {
  const b = document.createElement("button");
  b.type = "button";
  b.textContent = label;
  b.addEventListener("click", onClick);
  td.appendChild(b);
}

function flash(text, isError) {
  msg.textContent = text;
  msg.className = isError ? "hint error" : "hint";
}

async function load() {
  state = await call("GET", "/api/admin/state");
  const roomName = Object.fromEntries(state.rooms.map((r) => [r.id, r.name]));

  const rtbody = document.querySelector("#rooms-table tbody");
  rtbody.innerHTML = "";
  state.rooms.forEach((r) => {
    const tr = document.createElement("tr");
    cell(tr, r.name);
    cell(tr, r.capacity);
    cell(tr, `${r.open_time}-${r.close_time}`);
    cell(tr, `${r.min_dwell_seconds}s`);
    cell(tr, state.cameras.filter((c) => c.room_id === r.id).length);
    const actions = cell(tr, "");
    button(actions, "Edit", () => fillForm(roomForm, r));
    button(actions, "Delete", () => remove(`/api/admin/rooms/${r.id}`, `room ${r.name}`));
    rtbody.appendChild(tr);
  });

  const select = cameraForm.room_id;
  const selected = select.value;
  select.innerHTML = "";
  state.rooms.forEach((r) => select.add(new Option(r.name, r.id)));
  if (selected) select.value = selected;

  const ctbody = document.querySelector("#cameras-table tbody");
  ctbody.innerHTML = "";
  state.cameras.forEach((c) => {
    const tr = document.createElement("tr");
    cell(tr, roomName[c.room_id]);
    cell(tr, c.name);
    cell(tr, c.url);
    if (c.mode === "entrance") cell(tr, c.line ? "entrance line" : "entrance - no line yet").className = c.line ? "" : "bad";
    else cell(tr, c.zone ? `${c.zone.length} points` : "whole frame");
    const status = !c.enabled ? "disabled" : c.online ? "online" : `offline${c.last_error ? " - " + c.last_error : ""}`;
    cell(tr, status).className = c.online ? "ok" : "bad";
    const actions = cell(tr, "");
    button(actions, "Edit", () => fillForm(cameraForm, { ...c, url: "" }));
    button(actions, c.mode === "entrance" ? "Line" : "Zone", () => openZone(c));
    button(actions, "Delete", () => remove(`/api/admin/cameras/${c.id}`, `camera ${c.name}`));
    ctbody.appendChild(tr);
  });
}

function fillForm(form, obj) {
  Object.entries(obj).forEach(([k, v]) => {
    const input = form.elements[k];
    if (!input) return;
    if (input.type === "checkbox") input.checked = !!v;
    else input.value = v;
  });
  form.scrollIntoView({ behavior: "smooth" });
}

const pendingDelete = {};
async function remove(url, label) {
  // no confirm() dialogs: require a second click within 5s
  if (!pendingDelete[url] || Date.now() - pendingDelete[url] > 5000) {
    pendingDelete[url] = Date.now();
    flash(`Click Delete again to remove ${label}.`);
    return;
  }
  try {
    await call("DELETE", url);
    flash(`Deleted ${label}.`);
    load();
  } catch (e) {
    flash(e.message, true);
  }
}

roomForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = roomForm.elements;
  try {
    await call("POST", "/api/admin/rooms", {
      id: f.id.value ? Number(f.id.value) : null,
      name: f.name.value, capacity: Number(f.capacity.value),
      open_time: f.open_time.value, close_time: f.close_time.value,
      min_dwell_seconds: Number(f.min_dwell_seconds.value),
    });
    roomForm.reset();
    flash("Room saved.");
    load();
  } catch (err) {
    flash(err.message, true);
  }
});

cameraForm.addEventListener("submit", async (e) => {
  e.preventDefault();
  const f = cameraForm.elements;
  try {
    await call("POST", "/api/admin/cameras", {
      id: f.id.value ? Number(f.id.value) : null,
      room_id: Number(f.room_id.value), name: f.name.value, url: f.url.value, enabled: f.enabled.checked,
      mode: f.mode.value,
    });
    cameraForm.reset();
    flash("Camera saved. The capture service picks up changes within 30s.");
    load();
  } catch (err) {
    flash(err.message, true);
  }
});

// --- zone editor ------------------------------------------------------------
const img = document.getElementById("zone-img");
const canvas = document.getElementById("zone-canvas");
let previewTimer = null;

const isEntrance = () => zoneCamera && zoneCamera.mode === "entrance";

function openZone(camera) {
  zoneCamera = camera;
  if (isEntrance()) zonePoints = camera.line ? [camera.line.a, camera.line.b, camera.line.inside].map((p) => [...p]) : [];
  else zonePoints = camera.zone ? camera.zone.map((p) => [...p]) : [];
  document.getElementById("zone-title").textContent = isEntrance() ? "Entrance line" : "Zone";
  document.getElementById("zone-hint-zone").hidden = isEntrance();
  document.getElementById("zone-hint-entrance").hidden = !isEntrance();
  document.getElementById("zone-remove").hidden = isEntrance();
  document.getElementById("zone-camera-name").textContent = camera.name;
  document.getElementById("zone-editor").hidden = false;
  refreshPreview();
  clearInterval(previewTimer);
  previewTimer = setInterval(refreshPreview, 30000);
  document.getElementById("zone-editor").scrollIntoView({ behavior: "smooth" });
}

function refreshPreview() {
  img.src = `/camera-preview/${zoneCamera.id}?t=${Date.now()}`;
}

function draw() {
  canvas.width = img.clientWidth;
  canvas.height = img.clientHeight;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, canvas.width, canvas.height);
  if (!zonePoints.length) return;
  if (isEntrance()) return drawLine(ctx);
  ctx.beginPath();
  zonePoints.forEach(([x, y], i) => {
    const px = x * canvas.width, py = y * canvas.height;
    i ? ctx.lineTo(px, py) : ctx.moveTo(px, py);
  });
  ctx.closePath();
  ctx.fillStyle = "rgba(59,130,246,0.25)";
  ctx.strokeStyle = "#3b82f6";
  ctx.lineWidth = 2;
  ctx.fill();
  ctx.stroke();
  zonePoints.forEach(([x, y]) => ctx.fillRect(x * canvas.width - 3, y * canvas.height - 3, 6, 6));
}

function drawLine(ctx) {
  const [a, b, inside] = zonePoints.map(([x, y]) => [x * canvas.width, y * canvas.height]);
  ctx.lineWidth = 3;
  ctx.strokeStyle = "#f97316";
  ctx.fillStyle = "#f97316";
  ctx.fillRect(a[0] - 4, a[1] - 4, 8, 8);
  if (b) {
    ctx.beginPath();
    ctx.moveTo(...a);
    ctx.lineTo(...b);
    ctx.stroke();
    ctx.fillRect(b[0] - 4, b[1] - 4, 8, 8);
  }
  if (inside) {
    // arrow from the middle of the line towards the inside marker
    const m = [(a[0] + b[0]) / 2, (a[1] + b[1]) / 2];
    ctx.strokeStyle = ctx.fillStyle = "#22c55e";
    ctx.beginPath();
    ctx.moveTo(...m);
    ctx.lineTo(...inside);
    ctx.stroke();
    ctx.beginPath();
    ctx.arc(inside[0], inside[1], 6, 0, 2 * Math.PI);
    ctx.fill();
    ctx.font = "bold 14px sans-serif";
    ctx.fillText("IN", inside[0] + 9, inside[1] + 5);
  }
}

img.addEventListener("load", draw);
window.addEventListener("resize", draw);
canvas.addEventListener("click", (e) => {
  const rect = canvas.getBoundingClientRect();
  // snap to the frame edge so people cut off at the border (feet at the edge) stay inside the zone
  const snap = (v) => (v < 0.03 ? 0 : v > 0.97 ? 1 : v);
  if (isEntrance() && zonePoints.length >= 3) return flash("Line already complete - use Undo or Clear to change it.", true);
  zonePoints.push([snap((e.clientX - rect.left) / rect.width), snap((e.clientY - rect.top) / rect.height)]);
  draw();
});
document.getElementById("zone-undo").addEventListener("click", () => { zonePoints.pop(); draw(); });
document.getElementById("zone-clear").addEventListener("click", () => { zonePoints = []; draw(); });
document.getElementById("zone-close").addEventListener("click", () => {
  clearInterval(previewTimer);
  document.getElementById("zone-editor").hidden = true;
});
async function saveZone(points, message) {
  const body = { id: zoneCamera.id, room_id: zoneCamera.room_id, name: zoneCamera.name, enabled: zoneCamera.enabled };
  if (isEntrance()) body.line = points.length ? { a: points[0], b: points[1], inside: points[2] } : null;
  else body.zone = points.length ? points : null;
  try {
    await call("POST", "/api/admin/cameras", body);
    if (isEntrance()) zoneCamera.line = body.line;
    else zoneCamera.zone = points.length ? points.map((p) => [...p]) : null;
    flash(message);
    load();
  } catch (err) {
    flash(err.message, true);
  }
}
document.getElementById("zone-save").addEventListener("click", () => {
  if (isEntrance()) {
    if (zonePoints.length !== 3) return flash("Click 2 points for the line, then 1 point on the inside of the room.", true);
    return saveZone(zonePoints, "Entrance line saved. The camera restarts with it within 30s.");
  }
  if (zonePoints.length && zonePoints.length < 3) return flash("A zone needs at least 3 points.", true);
  saveZone(zonePoints, "Zone saved. The camera restarts with the new zone within 30s.");
});
document.getElementById("zone-remove").addEventListener("click", () => {
  if (!confirm(`Remove the zone for ${zoneCamera.name}? It will count people in the whole frame.`)) return;
  zonePoints = [];
  draw();
  saveZone([], "Zone removed - camera now counts the whole frame (applies within 30s).");
});

load();
setInterval(load, 30000);
