# Occupancy Monitor: Documentation

A proof of concept that counts people in rooms from CCTV/RTSP camera streams. It runs YOLO person
detection and ByteTrack tracking locally, stores the counts in SQLite, and serves a
login-protected web dashboard with live figures, hourly and daily stats, and daily reports.

| Document | Contents |
|---|---|
| [architecture.md](architecture.md) | How the pieces fit together, data flow, and the two counting modes (zone and entrance) |
| [modules.md](modules.md) | Every Python module, template and script: what it does and its key functions |
| [database.md](database.md) | Tables, columns, and how each one is written and read |
| [configuration.md](configuration.md) | Every setting in `config.py` / `.env` and when to change it |
| [operations.md](operations.md) | Install, deploy, restart, logs, and troubleshooting (DNS, YouTube streams, CPU) |
| [azure-integration-plan.md](azure-integration-plan.md) | Plan for moving to a serverless Azure design with Microsoft Foundry (agent, Azure ML) and a serverless cloud detector |

## At a glance

```
cameras (RTSP / YouTube test streams / video files)
        │
        ▼
app.py  ── capture service (systemd: occupancy-capture)
  Supervisor ── one CameraWorker thread per enabled camera
     CameraWorker: read frames → YOLO (detector.py) → ByteTrack → counting
        zone camera:     tracker.VisitTracker → visit rows (in/out of a polygon)
        entrance camera: tracker.LineCounter  → door_event rows (line crossings)
  every 30 s: one room_occupancy sample per room
        │
        ▼
occupancy.db (SQLite, database.py)
        │
        ▼
dashboard/app.py ── web service (systemd: occupancy-dashboard, gunicorn on 127.0.0.1:5000)
  /          live page (current count, hourly, 7 days, last 24 h with in/out)
  /reports   daily reports + CSV export (reports.py)
  /admin     rooms, cameras, zones and entrance lines
        │
        ▼
Cloudflare Tunnel (cloudflared) → public HTTPS subdomain
```

Project root: `/home/yolo/yolo`. The session handover notes are in `HANDOVER.md`, and the
original spec is in `README.md`.
