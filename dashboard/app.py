import bisect
import json
import logging
import re
import sys
from datetime import datetime, timedelta
from functools import wraps
from logging.handlers import RotatingFileHandler
from pathlib import Path

from flask import Flask, Response, jsonify, render_template, request, session, redirect, url_for, send_from_directory, abort
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import database
import reports

app = Flask(__name__)
app.secret_key = config.FLASK_SECRET_KEY
app.config.update(
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=not config.DASHBOARD_DEBUG,
    PERMANENT_SESSION_LIFETIME=timedelta(hours=config.SESSION_LIFETIME_HOURS),
)

log = logging.getLogger("dashboard.security")
log.setLevel(logging.INFO)
_log_handler = RotatingFileHandler(
    "logs/dashboard.log", maxBytes=config.LOG_MAX_BYTES, backupCount=config.LOG_BACKUP_COUNT
)
_log_handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
log.addHandler(_log_handler)


@app.after_request
def set_security_headers(response):
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def utc_iso(dt):
    """DB times are naive UTC; the Z makes browsers convert them to the viewer's local time."""
    return dt.isoformat() + "Z" if dt else None


def get_client_ip():
    cf_ip = request.headers.get("CF-Connecting-IP")
    if cf_ip:
        return cf_ip
    return request.remote_addr


def snapshot_url_for(record):
    if not record.snapshot_path:
        return None
    age = (datetime.utcnow() - record.timestamp).total_seconds()
    if age > config.SNAPSHOT_RETENTION_SECONDS:
        return None
    return url_for("snapshot", filename=record.snapshot_path)


def login_required(view):
    @wraps(view)
    def wrapped(*args, **kwargs):
        if not session.get("logged_in"):
            return redirect(url_for("login", next=request.path))
        return view(*args, **kwargs)
    return wrapped


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    client_ip = get_client_ip()

    if request.method == "POST":
        window_start = datetime.utcnow() - timedelta(seconds=config.LOGIN_LOCKOUT_WINDOW_SECONDS)
        recent_failures = database.count_recent_login_failures(client_ip, since=window_start)

        if recent_failures >= config.LOGIN_MAX_ATTEMPTS:
            log.warning("SECURITY blocked_login ip=%s reason=rate_limited failures=%d", client_ip, recent_failures)
            error = "Too many failed login attempts. Try again later."
            return render_template("login.html", error=error, next=request.args.get("next", "")), 429

        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == config.DASHBOARD_USERNAME and check_password_hash(config.DASHBOARD_PASSWORD_HASH, password):
            database.record_login_attempt(client_ip, username, success=True)
            session.permanent = True
            session["logged_in"] = True
            log.info("SECURITY successful_login ip=%s user=%s", client_ip, username)
            next_path = request.form.get("next") or url_for("index")
            return redirect(next_path)

        database.record_login_attempt(client_ip, username, success=False)
        log.warning("SECURITY failed_login ip=%s user=%s", client_ip, username)
        error = "Invalid username or password"
    return render_template("login.html", error=error, next=request.args.get("next", ""))


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


def current_room():
    rooms = database.list_rooms()
    room_id = request.args.get("room", type=int)
    room = next((r for r in rooms if r.id == room_id), rooms[0] if rooms else None)
    if room is None:
        abort(404)
    return room, rooms


@app.context_processor
def inject_rooms():
    return {"all_rooms": database.list_rooms() if session.get("logged_in") else []}


@app.route("/")
@login_required
def index():
    room, _ = current_room()
    return render_template("index.html", room_name=room.name, room_capacity=room.capacity, room_id=room.id)


@app.route("/api/current")
@login_required
def current():
    latest = database.get_latest(current_room()[0].name)
    if latest is None:
        return jsonify({"people_count": 0, "occupancy_percent": 0, "timestamp": None, "snapshot_url": None})
    return jsonify({
        "people_count": latest.people_count,
        "occupancy_percent": float(latest.occupancy_percent),
        "timestamp": utc_iso(latest.timestamp),
        "snapshot_url": snapshot_url_for(latest),
    })


@app.route("/api/history")
@login_required
def history():
    room = current_room()[0]
    now = datetime.utcnow()
    since = now - timedelta(days=1)
    records = database.get_history(room.name, since=since)
    ins, outs = database.get_in_out_times(room.id, since, now)
    ins.sort()
    outs.sort()

    def between(times, lo, hi):  # events in (lo, hi]
        return bisect.bisect_right(times, hi) - bisect.bisect_right(times, lo)

    samples, prev = [], since
    for r in records:
        samples.append({
            "timestamp": utc_iso(r.timestamp),
            "people_count": r.people_count,
            "occupancy_percent": float(r.occupancy_percent),
            "snapshot_url": snapshot_url_for(r),
            "went_in": between(ins, prev, r.timestamp),  # since the previous sample
            "went_out": between(outs, prev, r.timestamp),
        })
        prev = r.timestamp
    return jsonify({"samples": samples, "went_in": len(ins), "went_out": len(outs)})


@app.route("/snapshot/<filename>")
@login_required
def snapshot(filename):
    safe_name = secure_filename(filename)
    if safe_name != filename:
        abort(404)
    return send_from_directory(config.SNAPSHOT_DIR, safe_name)


@app.route("/api/stats/peak")
@login_required
def peak():
    all_time = database.get_peak(current_room()[0].name)
    today_start = reports.local_to_utc(reports.today_local(), "00:00")
    today = database.get_peak(current_room()[0].name, since=today_start)

    def serialize(r):
        if r is None:
            return None
        return {
            "people_count": r.people_count,
            "occupancy_percent": float(r.occupancy_percent),
            "timestamp": utc_iso(r.timestamp),
        }

    return jsonify({"all_time": serialize(all_time), "today": serialize(today)})


@app.route("/api/stats/hourly")
@login_required
def hourly():
    day = reports.parse_date(request.args.get("date"), reports.today_local())
    day_start = reports.local_to_utc(day, "00:00")
    day_end = reports.local_to_utc(day + timedelta(days=1), "00:00")
    return jsonify(database.get_hourly_stats(current_room()[0].name, day_start, day_end, reports.TZ))


@app.route("/api/stats/daily")
@login_required
def daily():
    days = int(request.args.get("days", 7))
    since = reports.local_to_utc(reports.today_local() - timedelta(days=days - 1), "00:00")
    return jsonify(database.get_daily_stats(current_room()[0].name, since, reports.TZ))


# --- reports -----------------------------------------------------------------

def report_rows():
    room, _ = current_room()
    today = reports.today_local()
    date_from = reports.parse_date(request.args.get("from"), today - timedelta(days=6))
    date_to = reports.parse_date(request.args.get("to"), today)
    if (date_to - date_from).days > 366:
        abort(400)
    hhmm = re.compile(r"^\d{2}:\d{2}$")
    open_time = request.args.get("open") if hhmm.match(request.args.get("open", "")) else None
    close_time = request.args.get("close") if hhmm.match(request.args.get("close", "")) else None
    return room, reports.daily_report(room, date_from, date_to, open_time, close_time)


@app.route("/reports")
@login_required
def reports_page():
    room, _ = current_room()
    today = reports.today_local()
    return render_template("reports.html", room=room, columns=reports.CSV_COLUMNS, min_dwell=room.min_dwell_seconds,
                           entrance=database.room_has_entrance(room.id),
                           date_from=(today - timedelta(days=6)).isoformat(), date_to=today.isoformat())


@app.route("/api/reports/daily")
@login_required
def report_daily():
    return jsonify(report_rows()[1])


@app.route("/api/reports/daily.csv")
@login_required
def report_daily_csv():
    room, rows = report_rows()
    filename = f"occupancy_{secure_filename(room.name)}_{request.args.get('from', '')}_{request.args.get('to', '')}.csv"
    return Response(reports.to_csv(rows), mimetype="text/csv",
                    headers={"Content-Disposition": f"attachment; filename={filename}"})


# --- admin: rooms & cameras ---------------------------------------------------

def mask_url(url):
    url = re.sub(r"(//[^:/@]+:)[^@]+@", r"\1***@", url)
    return re.sub(r"(password=)[^&_]+", r"\1***", url, flags=re.I)


def json_body():
    # JSON-only writes: browsers can't send cross-site JSON without a CORS preflight
    if not request.is_json:
        abort(415)
    return request.get_json()


@app.route("/admin")
@login_required
def admin_page():
    return render_template("admin.html", max_cameras=config.MAX_CAMERAS_PER_ROOM, min_dwell_default=config.MIN_DWELL_SECONDS)


@app.route("/api/admin/state")
@login_required
def admin_state():
    online_after = datetime.utcnow() - timedelta(seconds=config.SAMPLE_INTERVAL_SECONDS * 2 + config.CAMERA_OFFLINE_SECONDS)
    return jsonify({
        "rooms": [{"id": r.id, "name": r.name, "capacity": r.capacity, "open_time": r.open_time,
                   "close_time": r.close_time, "min_dwell_seconds": r.min_dwell_seconds,
                   "door_count": r.door_count} for r in database.list_rooms()],
        "cameras": [{"id": c.id, "room_id": c.room_id, "name": c.name, "url": mask_url(c.url), "zone": c.zone_points,
                     "mode": c.mode, "line": c.line_def, "frame_interval": c.frame_interval,
                     "enabled": c.enabled, "online": bool(c.enabled and c.last_seen and c.last_seen > online_after),
                     "last_seen": utc_iso(c.last_seen), "last_error": c.last_error}
                    for c in database.list_cameras()],
    })


def admin_error(exc):
    return jsonify({"error": str(exc)}), 400


@app.route("/api/admin/rooms", methods=["POST"])
@login_required
def admin_save_room():
    data = json_body()
    try:
        name = data["name"].strip()
        capacity = int(data["capacity"])
        open_time, close_time = data.get("open_time", "07:00"), data.get("close_time", "17:00")
        if not name or capacity < 1 or not re.match(r"^\d{2}:\d{2}$", open_time) or not re.match(r"^\d{2}:\d{2}$", close_time):
            raise ValueError("Name, capacity >= 1 and HH:MM times are required")
        min_dwell = int(data.get("min_dwell_seconds", config.MIN_DWELL_SECONDS))
        if not 0 <= min_dwell <= 3600:
            raise ValueError("Min stay must be between 0 and 3600 seconds")
        room_id = database.save_room(data.get("id"), name=name[:50], capacity=capacity,
                                     open_time=open_time, close_time=close_time, min_dwell_seconds=min_dwell)
    except Exception as exc:
        return admin_error(exc)
    log.info("ADMIN save_room id=%s ip=%s", room_id, get_client_ip())
    return jsonify({"id": room_id})


@app.route("/api/admin/rooms/<int:room_id>", methods=["DELETE"])
@login_required
def admin_delete_room(room_id):
    try:
        database.delete_room(room_id)
    except Exception as exc:
        return admin_error(exc)
    log.info("ADMIN delete_room id=%s ip=%s", room_id, get_client_ip())
    return jsonify({"ok": True})


@app.route("/api/admin/cameras", methods=["POST"])
@login_required
def admin_save_camera():
    data = json_body()
    try:
        fields = {"room_id": int(data["room_id"]), "name": data["name"].strip()[:50],
                  "enabled": bool(data.get("enabled", True))}
        if data.get("url"):  # blank on edit = keep the stored URL (it's shown masked)
            fields["url"] = data["url"].strip()[:500]
        elif not data.get("id"):
            raise ValueError("Stream URL is required")
        if "zone" in data:
            zone = data["zone"]
            if zone is not None:
                zone = [[min(1.0, max(0.0, float(x))), min(1.0, max(0.0, float(y)))] for x, y in zone]
                if len(zone) < 3:
                    raise ValueError("A zone needs at least 3 points")
            fields["zone"] = json.dumps(zone) if zone else None
        if "frame_interval" in data:  # blank/None = default rate for the camera type
            interval = data["frame_interval"]
            interval = float(interval) if interval not in (None, "") else None
            if interval is not None and not 0.2 <= interval <= 600:
                raise ValueError("Analyse every: between 0.2 and 600 seconds (or blank for the default)")
            fields["frame_interval"] = interval
        if "mode" in data:
            if data["mode"] not in ("zone", "entrance"):
                raise ValueError("Camera type must be zone or entrance")
            fields["mode"] = data["mode"]
        if "line" in data:
            line = data["line"]
            if line is not None:
                line = {k: [min(1.0, max(0.0, float(line[k][0]))), min(1.0, max(0.0, float(line[k][1])))]
                        for k in ("a", "b", "inside")}
                if line["a"] == line["b"]:
                    raise ValueError("The entrance line needs two different points")
            fields["line"] = json.dumps(line) if line else None
        if not fields["name"] or database.get_room(fields["room_id"]) is None:
            raise ValueError("Camera name and a valid room are required")
        camera_id = database.save_camera(data.get("id"), **fields)
    except Exception as exc:
        return admin_error(exc)
    log.info("ADMIN save_camera id=%s ip=%s", camera_id, get_client_ip())
    return jsonify({"id": camera_id})


@app.route("/api/admin/cameras/<int:camera_id>", methods=["DELETE"])
@login_required
def admin_delete_camera(camera_id):
    database.delete_camera(camera_id)
    log.info("ADMIN delete_camera id=%s ip=%s", camera_id, get_client_ip())
    return jsonify({"ok": True})


@app.route("/camera-preview/<int:camera_id>")
@login_required
def camera_preview(camera_id):
    response = send_from_directory(config.SNAPSHOT_DIR, f"camera{camera_id}_latest.jpg", max_age=0)
    response.headers["Cache-Control"] = "no-store"
    return response


if __name__ == "__main__":
    database.init_db()
    app.run(host=config.DASHBOARD_HOST, port=config.DASHBOARD_PORT, debug=config.DASHBOARD_DEBUG)
