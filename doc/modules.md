# Modules

Every file in the project, with what it does and its main functions. Paths are relative to
`/home/yolo/yolo`.

---

## Capture service

### `app.py`: capture supervisor and camera workers
Entry point of the `occupancy-capture` service (`python3 app.py`).

| Item | Purpose |
|---|---|
| `main()` | Sets the analysis threads per camera (`torch.set_num_threads(TORCH_THREADS)`), starts the `Supervisor`, stops cleanly on SIGTERM |
| `Supervisor.run()` | Main loop: `database.init_db()`, then every 30 s `sync_workers()` and `sample_rooms()`, and purges old snapshots |
| `Supervisor.sync_workers()` | Reads enabled cameras from the DB. Starts a worker for each new camera, and stops and restarts any camera whose URL, zone, room or `updated_at` changed. Refreshes each worker's room object so capacity and min-stay edits apply live |
| `Supervisor.sample_rooms()` | One `room_occupancy` row per room. Zone rooms: sum of camera counts. Entrance rooms: `room.door_count`. Skipped if a counting camera is offline |
| `CameraWorker` (thread `cam<id>`) | Opens the stream, reconnects on failure, selects frames (see architecture), runs detection, ByteTrack and counting, saves a snapshot when the count changes, and every 30 s updates camera status and writes the live preview image `snapshots/camera<id>_latest.jpg` |
| `CameraWorker.online()` | True if a frame was processed within `CAMERA_OFFLINE_SECONDS` |
| `zone_roi()` / `line_roi()` | The part of the frame to run detection on: the x-range of the zone or line plus a 10 % margin, full height. Returns `None` (whole frame) if that covers ≥ 90 % of the width |
| `preview_filename()` / `is_local_file()` | Helpers |

ByteTrack note: supervision keeps lost tracks for `int(frame_rate/30 × lost_track_buffer)`
frames, so the buffer is passed as `EXIT_GRACE_SECONDS × 30` to get the intended 20 s at any
frame rate.

### `detector.py`: YOLO wrapper
`PersonDetector(model_path, confidence)` loads the Ultralytics YOLO model once per camera.
- `detect_sv(frame, roi=None, imgsz=None)` → `supervision.Detections` of people only. With
  `roi`, it detects on that crop and shifts the boxes back to full-frame coordinates. `imgsz` is
  the model input size (the image is scaled up or down to fit).
- `detect(frame)` is the older list-of-tuples API, kept for manual testing.

### `tracker.py`: turning tracks into counts
- **`VisitTracker`** (zone cameras): `update(detections, w, h, now)` returns the number of
  confirmed people in the zone. It opens a `visit` once a person has stayed `min_dwell_seconds`,
  and closes it when they have been gone for `EXIT_GRACE_SECONDS`. `_relink()` lets a new
  ByteTrack ID inherit a recently lost person within `REACQUIRE_DISTANCE`. IDs still visible in
  the frame are never re-linked.
- **`LineCounter`** (entrance cameras): journey counting. Each track keeps `origin`, `side` and
  `changed_at`. `update(...)` returns the crossings completed in this frame (+1 / −1), meaning
  the person has been on the new side for `JOURNEY_SETTLE_SECONDS` or has vanished there, and
  writes each one via `database.record_door_event()` with the crossing time. `side(px, py, w, h)` returns
  +1 inside, −1 outside, or `None` (within the hysteresis band, or inside the room but beyond
  the doorway's ends).

### `source.py`: stream URL resolution
- `is_youtube_url(url)`
- `resolve_stream_url(url)`: YouTube page URLs are turned into a direct media URL with yt-dlp
  (for testing). RTSP URLs and file paths are passed through unchanged.

### `occupancy.py`
`record(room, people_count, snapshot_path)` computes occupancy % against the room capacity and
writes a `room_occupancy` row.

### `snapshots.py`
- `blur_face_regions(frame, detections)`: pixelates the top `FACE_BLUR_FRACTION` of each person
  box, which approximates the face.
- `save_snapshot(frame, name, timestamp, detections)`: saves a JPEG into `snapshots/`, blurring
  faces if `SNAPSHOT_BLUR_FACES`.
- `purge_old_snapshots()`: deletes files older than `SNAPSHOT_RETENTION_SECONDS` (1 h).

### `config.py`
All settings, loaded from `.env` with python-dotenv. See [configuration.md](configuration.md).

---

## Shared data layer

### `database.py`: SQLAlchemy models and queries
Models: `RoomOccupancy`, `Room`, `Camera`, `Visit`, `DoorEvent`, `LoginAttempt` (see
[database.md](database.md)).

| Group | Functions |
|---|---|
| Setup | `init_db()` creates tables and adds new columns to older databases (`ALTER TABLE`). `_seed_default_room()` creates room A101 plus one camera from `.env` on first run. `_close_orphaned_visits()` closes visits left open by a crash |
| Samples | `record_occupancy`, `get_latest`, `get_history`, `get_peak`, `get_sample_times` |
| Local-time stats | `get_hourly_stats(room, start, end, tz)` and `get_daily_stats(room, since, tz)` group samples by local hour/day in Python (daylight-saving safe) |
| Rooms/cameras | `list_rooms`, `get_room`, `get_room_by_name`, `save_room`, `delete_room` (refuses if the room has cameras), `list_cameras`, `get_camera`, `save_camera` (max 2 cameras per room, bumps `updated_at`), `delete_camera`, `update_camera_status` |
| Visits (zone) | `open_visit`, `touch_visits`, `close_visit`, `get_visits` |
| Door events (entrance) | `record_door_event` (updates `room.door_count` and inserts the event in one transaction), `get_door_count`, `get_door_events`, `get_door_count_at`, `room_has_entrance` |
| In/out summary | `get_in_out_times(room_id, start, end)`: entry and exit times from door events (entrance rooms) or visits (zone rooms) |
| Login lockout | `record_login_attempt`, `count_recent_login_failures` |

### `reports.py`: daily reports
- `daily_report(room, date_from, date_to, open, close)` returns one row per day. `day_report()`
  builds the head-count step curve from `_visit_segments()` or `_door_segments()` and derives the
  statistics.
- `CSV_COLUMNS` / `to_csv(rows)` produce the CSV export.
- Time helpers: `local_to_utc(day, "HH:MM")`, `utc_to_local_hhmm()`, `today_local()`,
  `parse_date()`, `TZ`.

---

## Web dashboard

### `dashboard/app.py`: Flask app (served by gunicorn)
| Route | Purpose |
|---|---|
| `GET/POST /login`, `/logout` | Session login (scrypt password hash from `.env`). Lockout after 5 failures per IP in 15 min, stored in the DB so it works across gunicorn workers. The client IP comes from `CF-Connecting-IP` |
| `/` | Live page for `?room=<id>` |
| `/api/current` | Latest sample and snapshot |
| `/api/history` | Last 24 h of samples, each with `went_in` / `went_out` since the previous sample, plus 24 h totals |
| `/api/stats/peak`, `/api/stats/hourly`, `/api/stats/daily` | Peak today / all-time, per-hour and per-day aggregates (local time) |
| `/reports`, `/api/reports/daily`, `/api/reports/daily.csv` | Report page, JSON and CSV |
| `/admin`, `/api/admin/state`, `/api/admin/rooms`, `/api/admin/cameras` | Room and camera management, including zone polygons and entrance lines |
| `/snapshot/<file>`, `/camera-preview/<id>` | Authenticated image serving (`secure_filename`, no static access) |

Helpers: `login_required`, `current_room()` (from `?room=`), `utc_iso()` (adds `Z`),
`mask_url()` (hides credentials in stream URLs), `set_security_headers()`.

### Templates (`dashboard/templates/`)
- `login.html`: sign-in form.
- `_nav.html`: room selector and links, included on every page.
- `index.html`: live page: current count, peaks, *Today by Hour* (people inside), *Last 7 Days*,
  and *Last 24 Hours* (inside, occupancy, went in / went out).
- `reports.html`: report filters (room, dates, hours), table and CSV button.
- `admin.html`: rooms form (capacity, report hours, min stay), cameras form (type: zone or
  entrance), and the zone/line editor over the live preview.

### Scripts (`dashboard/static/`)
- `dashboard.js`: polls the APIs every 10 s and renders the tables and the inline SVG hourly
  chart (no external libraries).
- `reports.js`: runs the report and the CSV export.
- `admin.js`: CRUD for rooms and cameras. The editor draws the polygon (zone) or line + IN arrow
  (entrance). Points within 3 % of the frame edge snap onto the edge.
- `style.css`: styles.

---

## Deployment files
- `deploy/occupancy-capture.service`, `deploy/occupancy-dashboard.service`: systemd units
  (these copies are the master versions; copy them to `/etc/systemd/system/` after editing).
- `requirements.txt`: ultralytics, opencv-python-headless, flask, gunicorn, sqlalchemy,
  python-dotenv, supervision, yt-dlp, pandas. Tested with ultralytics 8.4, supervision 0.29,
  torch 2.12, Flask 3.1, SQLAlchemy 2.0.
- `models/yolov8n.pt`, `models/yolov8s.pt`: YOLO weights (yolov8s is the default).

## Runtime files (not version-controlled)
`.env` (secrets and settings), `occupancy.db`, `snapshots/`, `logs/app.log` and
`logs/dashboard.log` (rotating, 5 × 5 MB).
