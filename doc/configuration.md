# Configuration

Settings live in `config.py`. The ones marked **env** can be overridden in `/home/yolo/yolo/.env`
(a hidden file, not version-controlled). Restart `occupancy-capture` (and `occupancy-dashboard`
for dashboard or timezone settings) after changing `.env`.

## Detection and tracking
| Setting | Default | Env | Meaning / when to change |
|---|---|---|---|
| `YOLO_MODEL_PATH` | `models/yolov8s.pt` | ✓ | `yolov8n` is about 2× faster but misses more seated or partly hidden people |
| `YOLO_IMGSZ` | 1280 | ✓ | Model input size for zone cameras. Higher finds smaller people but costs more CPU |
| `CONFIDENCE_THRESHOLD` | 0.3 | ✓ | Lower finds more people but gives more false boxes |
| `TRACK_FPS` | 1 | ✓ | Frames per second analysed per zone camera |
| `ENTRANCE_TRACK_FPS` | 3 | ✓ | Frames per second for entrance cameras. People cross a door in 1–2 s |
| `ENTRANCE_IMGSZ` | 640 | ✓ | People at a door are large, so this is enough and about 3.5× faster |
| `TORCH_THREADS` | 2 | ✓ | CPU threads per camera for analysis |
| `ZONE_CROP_MARGIN` | 0.1 | | Margin around the zone/line x-range when cropping |
| `MIN_DWELL_SECONDS` | 60 | | Default min stay for new rooms (set per room on `/admin`) |
| `EXIT_GRACE_SECONDS` | 20 | | How long a person may vanish (occlusion) before counting as gone |
| `REACQUIRE_DISTANCE` | 0.15 | | How close (fraction of the frame diagonal) a new ID must be to inherit a lost person |
| `LINE_HYSTERESIS` | 0.012 | | How far past the line feet must be to change side |
| `JOURNEY_SETTLE_SECONDS` | 2 | ✓ | How long on the other side before a crossing counts. Higher ignores more hesitation but counts later |
| `DOOR_COUNT_ALLOW_NEGATIVE` | false | ✓ | `true` only for footpath tests where people cross both ways |

CPU cost per frame on one core (Xeon E5-2640 v4): n@640 ≈ 95 ms, n@1280 ≈ 370 ms,
s@640 ≈ 180 ms, s@1280 ≈ 815 ms.

## Rooms, sampling, health
| Setting | Default | Meaning |
|---|---|---|
| `SAMPLE_INTERVAL_SECONDS` | 30 | Room sample and camera status interval |
| `CAMERA_OFFLINE_SECONDS` | 15 | No frame for this long = offline, and the room sample is skipped |
| `CONFIG_POLL_SECONDS` | 30 | How often admin changes are picked up |
| `MAX_CAMERAS_PER_ROOM` | 2 | |
| `RECONNECT_AFTER_FAILURES` / `RECONNECT_BACKOFF_SECONDS` | 5 / 5 | Stream reconnection |
| `TIMEZONE` (env) | Europe/London | Local time for hours, days and reports. Applies to all rooms |
| `ROOM_NAME`, `ROOM_CAPACITY`, `RTSP_URL` (env) | A101, 8, – | Only used to create the first room on a fresh database |

## Snapshots and privacy
| Setting | Default | Meaning |
|---|---|---|
| `SNAPSHOT_BLUR_FACES` (env) | true | Pixelate the face area in snapshots and previews |
| `FACE_BLUR_FRACTION` | 0.25 | Top share of each person box that gets pixelated |
| `SNAPSHOT_RETENTION_SECONDS` | 3600 | Snapshots are deleted after this |

## Dashboard and security
| Setting | Default | Meaning |
|---|---|---|
| `DASHBOARD_USERNAME` (env) | admin | |
| `DASHBOARD_PASSWORD_HASH` (env) | – | Generate with `werkzeug.security.generate_password_hash` |
| `FLASK_SECRET_KEY` (env) | – | Session signing key. Keep it secret |
| `DASHBOARD_DEBUG` (env) | false | Never true in production |
| `LOGIN_MAX_ATTEMPTS` / `LOGIN_LOCKOUT_WINDOW_SECONDS` | 5 / 900 | Lockout per IP |
| `SESSION_LIFETIME_HOURS` | 12 | |
| `DASHBOARD_HOST` / `DASHBOARD_PORT` | 127.0.0.1 / 5000 | The actual bind address is in the systemd unit (gunicorn `--bind`). It must stay on localhost, because the lockout trusts `CF-Connecting-IP` |

## Logging
`LOG_MAX_BYTES` 5 MB × `LOG_BACKUP_COUNT` 5 per log file in `logs/`.
