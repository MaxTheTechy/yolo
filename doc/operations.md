# Operations

## Install (fresh host)
```bash
cd /home/yolo/yolo
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
# models/yolov8s.pt is downloaded by ultralytics on first use if missing
nano .env   # at least DASHBOARD_PASSWORD_HASH, FLASK_SECRET_KEY; optionally TIMEZONE, RTSP_URL (see configuration.md)
sudo cp deploy/*.service /etc/systemd/system/ && sudo systemctl daemon-reload
sudo systemctl enable --now occupancy-capture occupancy-dashboard
```
Public access goes through Cloudflare Tunnel: `sudo cloudflared service install <token>`, with the
public hostname pointing at `http://localhost:5000` in Cloudflare Zero Trust.

## Restarts
| Change | Action |
|---|---|
| Python code in capture (`app.py`, `tracker.py`, `detector.py`, `config.py`, `.env`) | `sudo systemctl restart occupancy-capture` |
| Dashboard code or templates | `sudo systemctl restart occupancy-dashboard`, then hard-refresh the browser (Ctrl+Shift+R) |
| New DB columns | Restart capture first (it migrates), wait about 10 s, then restart the dashboard |
| Rooms, cameras, zones, lines on `/admin` | Nothing: applied within 30 s |
| A `deploy/*.service` file | `sudo cp` to `/etc/systemd/system/`, `daemon-reload`, restart |

## Logs and health
```bash
journalctl -u occupancy-capture -f
tail -f logs/app.log          # "room=... people=...", "Entrance <cam>: IN/OUT", camera errors
tail -f logs/dashboard.log    # SECURITY-tagged login events
ss -tlnp | grep 5000          # must show 127.0.0.1:5000
uptime                        # load should stay well below the core count (8)
```
Camera status (online/offline plus last error) is also shown on `/admin`.

## Troubleshooting

**"Room X: camera offline, sample skipped"**: the camera delivered no frame for 15 s.
Check the stream URL, the network, and DNS (below). Gaps in the data are expected during outages.

**DNS outages** (for example "Temporary failure in name resolution" for YouTube): the host
resolves via the LAN router (`<router-ip>`). Compare `dig @<router-ip> host` with
`dig @1.1.1.1 host`. If only the router fails, add a fallback resolver on the Proxmox host
(`pct set <id> --nameserver "<router-ip> 1.1.1.1"`).

**YouTube test streams**: YouTube live (HLS) arrives in about 5 s bursts, so frames are chosen
by video position. YouTube also refuses some segments (403), which freezes the stream for minutes
at a time, so counts from YouTube are lower than the truth. Real RTSP cameras don't have this
problem. yt-dlp warns that it needs a JavaScript runtime (deno) for full YouTube support.

**Under-counting in zone mode**: check that the zone reaches the frame edges (points snap within
3 %), that neighbouring zones touch without overlapping, and consider `YOLO_IMGSZ`,
`CONFIDENCE_THRESHOLD` and camera resolution.

**Entrance not counting**: people must cross the line. Draw it across the threshold at floor
level, as wide as the door opening, with the inside point in the doorway. The log line
`Entrance <camera>: IN/OUT` shows every crossing.

**Lost dashboard password**: generate a new hash and replace `DASHBOARD_PASSWORD_HASH` in `.env`:
```bash
.venv/bin/python -c "from werkzeug.security import generate_password_hash as g; print(g('NEW-PASSWORD'))"
```
