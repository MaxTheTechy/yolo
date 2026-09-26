# Database

SQLite file `occupancy.db` (`DATABASE_URL`, default `sqlite:///occupancy.db`), accessed through
SQLAlchemy in `database.py`. All timestamps are **naive UTC**. `init_db()` creates missing tables
and adds new columns to older databases, so upgrades need no manual migration. After adding a
column, restart the capture service **before** the dashboard, because the dashboard doesn't run
`init_db()` under gunicorn.

## Tables

### `room`
| Column | Notes |
|---|---|
| `id`, `name` (unique), `capacity` | Capacity is used for occupancy % |
| `open_time`, `close_time` | Default report window, "HH:MM" local time |
| `min_dwell_seconds` | Zone rooms: shorter stays are ignored (default 60) |
| `door_count` | Entrance rooms: current ins − outs (clamped at 0 unless `DOOR_COUNT_ALLOW_NEGATIVE`) |

### `camera`
| Column | Notes |
|---|---|
| `id`, `room_id`, `name`, `url`, `enabled` | URL: RTSP, YouTube (testing) or a local file path. Shown masked in the UI |
| `mode` | `zone` or `entrance` |
| `frame_interval` | Seconds between analysed frames. NULL = default (`TRACK_FPS` / `ENTRANCE_TRACK_FPS`). For example 10 for a classroom |
| `zone` | JSON `[[x, y], …]` normalised 0..1. NULL = whole frame (zone mode) |
| `line` | JSON `{"a": [x, y], "b": [x, y], "inside": [x, y]}` (entrance mode) |
| `updated_at` | Changing it makes the Supervisor restart the camera |
| `last_seen`, `last_error` | Health, updated about every 30 s by the worker |

### `room_occupancy`: 30-second samples
`room_name`, `timestamp`, `people_count`, `occupancy_percent`, `snapshot_path` (only when the
count changed). Feeds the live page, hourly and 7-day stats, and report coverage.

### `visit`: zone mode stays
`room_id`, `camera_id`, `entered_at` (real arrival time), `last_seen_at` (refreshed every 30 s),
`exited_at` (NULL while inside). Written only after the min stay.

### `door_event`: entrance mode crossings
`room_id`, `camera_id`, `timestamp`, `direction` (+1 in / −1 out), `count_after` (room count
after this crossing). Reports rebuild the head-count curve from `count_after`.

### `login_attempt`
`ip`, `username`, `success`, `timestamp`. Used for the 5-in-15-minutes lockout.

## Useful queries

```sql
-- crossings today (UTC) per room
SELECT r.name, SUM(direction > 0) AS ins, SUM(direction < 0) AS outs
FROM door_event e JOIN room r ON r.id = e.room_id
WHERE e.timestamp >= date('now') GROUP BY r.name;

-- current counts
SELECT name, door_count FROM room;

-- reset an entrance room's count
UPDATE room SET door_count = 0 WHERE name = 'SIN';
```
The `sqlite3` CLI isn't installed, so use
`.venv/bin/python -c "import sqlite3; ..."` instead.
