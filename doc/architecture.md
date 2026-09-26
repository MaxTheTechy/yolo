# Architecture

## Processes

The system runs as two independent services on one Linux host (a Proxmox LXC container, 8 cores).
They share only the SQLite database file.

| Service | Entry point | Role |
|---|---|---|
| `occupancy-capture` | `app.py` | Reads cameras, detects and tracks people, writes counts and events |
| `occupancy-dashboard` | `dashboard/app.py` via gunicorn (2 workers) | Web UI and JSON API, read-mostly (the admin page writes room/camera config) |
| `cloudflared` | system service | Publishes `127.0.0.1:5000` on a Cloudflare subdomain. The dashboard is never exposed on the LAN |

Changes made on the admin page reach the capture service through the database: the capture
Supervisor re-reads rooms and cameras every `CONFIG_POLL_SECONDS` (30 s) and restarts any camera
whose settings changed.

## Capture pipeline (per camera)

```
open stream (source.resolve_stream_url: YouTube → direct URL, others unchanged)
  │
  ├─ frame selection
  │    RTSP:        grab continuously (drain buffer), process one frame every 1/fps s (wall clock)
  │    YouTube/HLS: process every Nth frame by video position (streams arrive in ~5 s bursts)
  │    video file:  play back in real time, loop at the end
  │
  ├─ detection   detector.PersonDetector.detect_sv(frame, roi, imgsz)
  │    YOLOv8 (default yolov8s), person class only, confidence ≥ 0.3
  │    roi = only the part of the frame around the zone / entrance line (faster, larger people)
  │
  ├─ tracking    supervision.ByteTrack → stable tracker_id per person across frames
  │
  └─ counting
       zone mode      tracker.VisitTracker   feet point inside polygon for ≥ min stay → visit
       entrance mode  tracker.LineCounter    feet point changes side of the line → IN / OUT event
```

Every 30 s (`SAMPLE_INTERVAL_SECONDS`) the Supervisor writes one `room_occupancy` row per room.
It skips the sample if any camera that counts for that room is offline (no frame for 15 s), so a
gap in the data means an outage, never a false zero.

## Counting modes

### Zone (people in view)
- Each camera has an optional polygon (normalised 0..1 coordinates, drawn on `/admin`). No
  polygon means the whole frame.
- A person is placed at their **feet** (bottom-centre of the box) and is inside the zone if that
  point is inside the polygon.
- A person counts only after staying `min_dwell_seconds` (set per room). A visit is then
  recorded from their real arrival time.
- A person who disappears for up to `EXIT_GRACE_SECONDS` (20 s) is still counted, which covers
  occlusion. When tracking gives a person a new ID nearby, it inherits the old ID's visit.
- Room count = **sum** of its cameras' counts. A room has at most 2 cameras, which must watch
  areas that don't overlap.

### Entrance (in/out line)
- The camera has a line `{a, b, inside}`: two points across the doorway at floor level, plus
  one point on the room side.
- **Journey counting** (the method dedicated overhead counters use): each tracked person has an
  origin side. A crossing becomes a `door_event` only once they have stayed on the other side for
  `JOURNEY_SETTLE_SECONDS` (2 s), or vanished there, for example through the door. The event is
  timestamped at the moment of crossing. Stepping across and back, or hovering on the line,
  counts nothing. Zig-zagging counts only the net result. Towards inside = IN (+1), otherwise
  OUT (−1).
- Hysteresis: feet must be `LINE_HYSTERESIS` (1.2 % of the frame diagonal) past the line, so
  jitter on the line never counts. "Outside" counts anywhere on the outer side. "Inside" only
  counts level with the doorway, so walking past the end of the line never counts as entering.
- Room count = `room.door_count` (ins − outs), clamped at 0 unless `DOOR_COUNT_ALLOW_NEGATIVE`.
  If a room has an entrance camera, its zone cameras are ignored for the count.
- Runs at `ENTRANCE_TRACK_FPS` (3) with `ENTRANCE_IMGSZ` (640), because people cross a door in
  1–2 s and appear large.

## Reports

`reports.py` builds each day's head-count curve from events, not from the 30 s samples:
- zone rooms: from `visit` rows (entered_at / exited_at)
- entrance rooms: from `door_event` rows (`count_after` after each crossing)

From the curve it derives entries, exits, max/min/avg, peak start/end, longest stretch at peak
and occupied time. Data coverage is the share of the window with 30 s samples.

## Time handling

- Everything is stored as **naive UTC** (`datetime.utcnow()`).
- The API marks timestamps as UTC (`…Z`), so the browser shows them in the viewer's local time.
- Days and hours for the hourly chart, the 7-day table, "peak today" and reports are cut at local
  midnight and local hours in `TIMEZONE` (default Europe/London), with summer time handled.

## Privacy

- Only numbers are kept long-term. Snapshots are saved only when a camera's count changes,
  pixelate the top 25 % of each person box (the face area), and are deleted after 1 h.
- Snapshots and live previews are served only through authenticated routes.
- This was agreed as a testing measure. Review it before using the system in production.
