# Handover — Classroom Occupancy PoC

Project lives at `/home/yolo/yolo` (not `/opt/yolo` — that directory stayed
root-owned and was abandoned early on). Original spec is in `README.md`.

## What's running

Two systemd services, both `enabled` (survive reboot) and `Restart=on-failure`:

- **`occupancy-capture.service`** — runs `app.py`: connects to the video
  source, samples a frame every `SAMPLE_INTERVAL_SECONDS` (30s), runs YOLOv8n
  person detection, writes a row to SQLite.
- **`occupancy-dashboard.service`** — gunicorn (`--workers 2 --bind
  127.0.0.1:5000`) serving `dashboard/app.py`. Bound to localhost only — not
  reachable directly over the LAN, only via the Cloudflare Tunnel.

A third service, **`cloudflared`**, was set up via `sudo cloudflared service
install <token>` to expose the dashboard at a subdomain on the user's
Cloudflare-managed domain (public hostname route configured in the Cloudflare
Zero Trust dashboard, pointing at `http://localhost:5000`). **Its status was
not confirmed in-session** — verify with `sudo systemctl status cloudflared`.

Unit files are version-controlled under `deploy/`; if you edit them, you must
re-`cp` into `/etc/systemd/system/` and `daemon-reload` for changes to take
effect (a plain `restart` is not enough).

### ⚠️ Not yet confirmed at end of last session

The last few restarts (dashboard rebind to `127.0.0.1`, capture service
picking up the face-blur change) were handed to the user to run, but no
output was pasted back before this handover was written. **First thing to do
in a new session: confirm both services are `active (running)`** and that
`ss -tlnp | grep 5000` shows `127.0.0.1:5000`, not `0.0.0.0:5000`.

## Why commands get printed instead of run directly

The agent's `Bash` tool runs in a sandbox that does **not** share state with
the user's real host — confirmed early on (a `chown` and later `systemctl`
checks showed no effect/visibility from the agent's shell). Anything needing
`sudo` (installs, systemd, firewall) has to be handed to the user as a
command block for them to run and paste output back. Don't assume a `sudo`
command "worked" without that confirmation loop.

## Multi-room / in-out tracking (added 2026-09-24)

- Rooms and cameras live in the DB (`room`, `camera` tables), managed at `/admin`.
  Max 2 cameras per room; each camera has a polygon zone (normalised 0..1,
  drawn on its live preview) and room count = **sum** of its cameras' zones.
  First run seeds room `A101` + one camera from the old `config`/`.env` values.
- `app.py` is now a supervisor: one `CameraWorker` thread per enabled camera
  (YOLO + ByteTrack at `TRACK_FPS`=2), re-reads the DB every 30s and restarts
  changed cameras. Room samples (`room_occupancy`) are still written every 30s,
  but only while all of a room's cameras are online (gaps = outage).
- `tracker.VisitTracker` writes `visit` rows (in/out). A person counts only
  after `MIN_DWELL_SECONDS` (60s) in the zone — then from their real entry time.
  `EXIT_GRACE_SECONDS` (20s) absorbs occlusion; new ByteTrack ids near a
  recently lost person inherit their visit (id-switch fix).
- `reports.py` + `/reports`: daily report per room for a local-time window
  (default room hours 07:00-17:00, `TIMEZONE` env, default Europe/London):
  entries/exits, max/min/avg, peak start/end, longest stretch at peak, coverage.
  CSV export at `/api/reports/daily.csv`.
- A local video file path works as a camera URL (loops in real time) — use
  this for testing instead of YouTube.

## Video source

`RTSP_URL` in `.env` is currently set to a **YouTube test video**
(`https://youtu.be/M3EYAY2MftI`, also saved in `video.link.txt`) instead of
the real camera, for detection-accuracy testing. `source.py` resolves
YouTube URLs to a direct stream via `yt-dlp` transparently — `app.py` doesn't
know the difference. The real camera URL is preserved as a commented-out line
directly above it in `.env`:

```
# RTSP_URL=rtsp://<camera-ip>:554/user=<user>_password=<password>&channel=1_stream=1.sdp?real_stream
RTSP_URL=https://youtu.be/M3EYAY2MftI
```

Swap back by commenting/uncommenting and restarting `occupancy-capture.service`.

Because it's a finite video, `app.py`'s reconnect logic treats end-of-video as
a read failure and restarts the stream — this is expected, not a bug.

## File map

```
app.py            capture loop: RTSP/YouTube → YOLO → occupancy → DB → snapshot
config.py         all settings, loads .env via python-dotenv
database.py       SQLAlchemy models (RoomOccupancy, LoginAttempt) + queries
detector.py       YOLOv8n person-detection wrapper
tracker.py        FrameCounter — Phase 1 just counts per-frame, no cross-frame
                  tracking yet (ByteTrack is a Phase 2 README goal)
occupancy.py      occupancy % calc + DB write
snapshots.py      save/blur/purge snapshot images
source.py         resolves YouTube URLs to direct stream URLs via yt-dlp
dashboard/app.py  Flask: auth, login lockout, all /api/* routes, /snapshot/<f>
dashboard/templates/, dashboard/static/   login page, main dashboard, JS/CSS
deploy/           systemd unit files (source of truth — copy to /etc to deploy)
snapshots/        runtime images, gitignored, auto-purged after 1h
logs/             rotating app.log / dashboard.log
occupancy.db       SQLite, gitignored
.env              gitignored: RTSP_URL, DASHBOARD_USERNAME,
                  DASHBOARD_PASSWORD_HASH, FLASK_SECRET_KEY
```

## Dashboard features

- Current occupancy, peak today / peak all-time, hourly bar chart (inline
  SVG, no external JS deps), 7-day daily table, 24h raw sample table.
- "Latest Snapshot" panel next to the hourly chart; raw-sample table links
  to the snapshot for that row when one exists.

## Security posture (added this round)

- Session login (Flask session cookie: `HttpOnly`, `Secure`, `SameSite=Lax`,
  12h lifetime), password hashed with werkzeug `scrypt`.
- **Login lockout**: 5 failed attempts from one IP within 15 minutes → `429`
  on *any* further attempt (even correct credentials) until the window ages
  out. Backed by a `login_attempt` DB table (not in-memory), so it's correct
  across gunicorn's 2 worker processes — an in-memory counter would have
  under-counted by splitting attempts across workers.
- Real client IP is read from the `CF-Connecting-IP` header. This is only
  trustworthy because gunicorn binds to `127.0.0.1` — if it were on `0.0.0.0`,
  anyone reaching the port directly could spoof that header to dodge the
  lockout or frame another IP. This was an explicit fix this round (see the
  network-binding decision in conversation history).
- Basic security headers (`X-Content-Type-Options`, `X-Frame-Options`,
  `Referrer-Policy`) via `after_request`.
- `DASHBOARD_DEBUG` env var gates Flask debug mode (defaults off); avoids the
  old hardcoded `debug=True`.
- **Explicitly skipped**: OS-level fail2ban / iptables banning. User chose
  app-level lockout only — revisit if brute-forcing becomes a real concern.

Dashboard login: username from `DASHBOARD_USERNAME` in `.env`; the password was generated once and
shown in chat — **not stored in plaintext anywhere** (only the scrypt hash lives in
`.env`). If lost, regenerate with `werkzeug.security.generate_password_hash`
and replace `DASHBOARD_PASSWORD_HASH` in `.env`, then restart the dashboard
service.

## Snapshot / GDPR notes

The original README explicitly recommended **not** storing images, only
metrics. This was knowingly overridden, scoped narrowly per the user's
request:

- A snapshot is saved only when the detected people count *changes* (not
  every sample).
- Auto-purged after `SNAPSHOT_RETENTION_SECONDS` (1h) — purge runs every
  sample cycle in `app.py`.
- Faces are pixelated before saving (`snapshots.blur_face_regions`) —
  approximates the face as the top `FACE_BLUR_FRACTION` (25%) of each
  person's bounding box, not a dedicated face detector. Toggle via
  `SNAPSHOT_BLUR_FACES` env var. Good enough for upright/forward-facing
  people; will under/over-blur for unusual poses. A dedicated face detector
  (e.g. OpenCV YuNet) was discussed as a future upgrade if accuracy here
  matters more.
- Snapshots are only servable through the authenticated `/snapshot/<filename>`
  route (`secure_filename`-validated against path traversal) — never static
  files, so they're covered by the same login as everything else.

This was framed by the user as **testing-only**, to validate detection
accuracy before deciding on a longer-term policy — don't assume it's the
final design without checking in.

## Known limitations (discussed, not fixed)

- **Single-frame double-counting**: YOLO can occasionally produce two boxes
  for one physical person (occlusion fragmenting a body, mirrors/screens/
  posters showing a "person" image, NMS edge cases at the current 0.4
  confidence threshold). Not compounding across samples since each 30s
  sample is independent (no identity tracking yet). `yolov8n` is the
  smallest/fastest model — `yolov8s`/`m` would reduce this at the cost of
  slower inference, if it turns out to matter.
- No multi-frame tracking (ByteTrack) yet — that's a stated Phase 2 README
  goal, not started.

## Useful commands

```bash
# Logs
journalctl -u occupancy-capture.service -f
journalctl -u occupancy-dashboard.service -f
tail -f /home/yolo/yolo/logs/dashboard.log   # SECURITY-tagged login events

# Restart after code changes (no unit file changes needed)
sudo systemctl restart occupancy-capture.service
sudo systemctl restart occupancy-dashboard.service

# Restart after editing a deploy/*.service file
sudo cp /home/yolo/yolo/deploy/<name>.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl restart <name>.service

# Manual run (debugging, outside systemd)
cd /home/yolo/yolo && source .venv/bin/activate
python3 app.py
gunicorn --bind 127.0.0.1:5000 dashboard.app:app
```

## Entrance (in/out line) cameras (added 2026-09-26)

- Camera `mode`: `zone` (count people visible in a polygon, as before) or `entrance`.
  Entrance cameras have a `line` = {a, b, inside} drawn at `/admin` → **Line** (2 clicks for the
  line at floor level, 1 click on the room side).
- `tracker.LineCounter`: a tracked person's feet changing side of the line = one `door_event`
  (+1 in / -1 out). Hysteresis `LINE_HYSTERESIS` (2% of diagonal) and doorway-extent check stop
  jitter and passers-by from counting; lost ByteTrack ids are re-linked like VisitTracker.
- Room count for a room with an entrance camera = `room.door_count` (ins - outs, clamped at 0 in
  `database.record_door_event`). The room's zone cameras are ignored for its count. No daily reset
  or manual correction (user chose clamp-at-0 only).
- Entrance cameras run at `ENTRANCE_TRACK_FPS` (default 3) and detect only around the line.
- Reports: entrance rooms are built from `door_event` (`reports._door_segments`).

## Detection tuning (2026-09-26)

- Defaults now yolov8s @ imgsz 1280, conf 0.3, TRACK_FPS 1, TORCH_THREADS 2 per camera — all
  overridable in `.env`. Zone cameras detect only in a crop around their zone (full height).
- Fixed: ByteTrack lost-track buffer was ~1s instead of EXIT_GRACE_SECONDS (supervision scales it
  by frame_rate/30).
- Container has 8 cores (was 4). YouTube HLS test streams stall with 403s on segments — see
  conversation; real RTSP cameras are unaffected.

## Documentation (2026-09-26)

Full docs in `doc/` (architecture, modules, database, configuration, operations, Azure integration plan).
- Entrance counting is journey-based (2026-09-26): a crossing counts after JOURNEY_SETTLE_SECONDS on the new side or vanishing there; hesitations and hovering count nothing.
- Shared DetectorPool (2026-09-26): all cameras share a few model copies (CPU) or one batched GPU model; memory no longer grows per camera. GPU used automatically when CUDA is present.
- Per-camera analysis interval (camera.frame_interval, admin "Analyse every (s)"), 2026-09-26. Online = stream delivering video, not last analysed frame.

## Pending at end of 2026-09-26 session

- User to run: `git push -u origin main` (public repo github.com/MaxTheTechy/yolo; agent push was blocked
  by permissions). Local commits ahead of origin.
- User to run capture then dashboard restart to pick up: detector pool, per-camera interval (new
  `camera.frame_interval` column — capture first so it migrates).
- Deferred by user ("not yet"): room_occupancy index + SQLite WAL, building overview page, camera-offline
  alerts, YouTube reader via yt-dlp+ffmpeg, GPU purchase (RTX A2000 12GB suggested).
- `.env` has `DOOR_COUNT_ALLOW_NEGATIVE=true` for the footpath test — remove for real rooms.
