import os

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

RTSP_URL = os.environ.get("RTSP_URL", "rtsp://username:password@camera-ip/stream")

DATABASE_URL = os.environ.get("DATABASE_URL", "sqlite:///occupancy.db")

SAMPLE_INTERVAL_SECONDS = 30
ROOM_CAPACITY = 8
ROOM_NAME = "A101"  # only used to seed the first room; rooms now live in the DB

# Tracking / visits
TRACK_FPS = float(os.environ.get("TRACK_FPS", "1"))  # frames per second fed to YOLO + ByteTrack, per camera
MIN_DWELL_SECONDS = 60           # people in the zone for less than this are ignored (e.g. deliveries)
EXIT_GRACE_SECONDS = 20          # how long a person may vanish (occlusion) before counted as gone
REACQUIRE_DISTANCE = 0.15        # max move (fraction of frame diagonal) to re-link a lost person to a new track id
MAX_CAMERAS_PER_ROOM = 2
ENTRANCE_TRACK_FPS = float(os.environ.get("ENTRANCE_TRACK_FPS", "3"))  # entrance cameras: people walk through a door in ~1-2s
ENTRANCE_IMGSZ = int(os.environ.get("ENTRANCE_IMGSZ", "640"))  # people at a door are large; ~3.5x faster than 1280
# testing on a footpath (people pass both ways): let the entrance count go negative instead of stopping at 0
DOOR_COUNT_ALLOW_NEGATIVE = os.environ.get("DOOR_COUNT_ALLOW_NEGATIVE", "false").lower() == "true"
# journey counting: a crossing counts once the person has stayed on the other side (or vanished
# there, e.g. through the door) this long; stepping across and back within it counts nothing
JOURNEY_SETTLE_SECONDS = float(os.environ.get("JOURNEY_SETTLE_SECONDS", "2"))
LINE_HYSTERESIS = 0.012          # a person must be this far (fraction of frame diagonal) past the line to change side
CAMERA_OFFLINE_SECONDS = 15      # no frame for this long = camera offline (room sample skipped)
CONFIG_POLL_SECONDS = 30         # how often the capture service checks the DB for camera changes
TIMEZONE = os.environ.get("TIMEZONE", "Europe/London")  # reporting hours (07:00-17:00) are in this zone

# Detection. Cost per frame on one core (E5-2640 v4): n@640 ~95ms, n@1280 ~370ms, s@640 ~180ms, s@1280 ~815ms
YOLO_MODEL_PATH = os.environ.get("YOLO_MODEL_PATH", "models/yolov8s.pt")
YOLO_IMGSZ = int(os.environ.get("YOLO_IMGSZ", "1280"))  # small/far/seated people need the higher resolution
PERSON_CLASS_ID = 0
CONFIDENCE_THRESHOLD = float(os.environ.get("CONFIDENCE_THRESHOLD", "0.3"))
TORCH_THREADS = int(os.environ.get("TORCH_THREADS", "2"))  # per camera; torch otherwise grabs every host core
ZONE_CROP_MARGIN = 0.1           # detect only around the zone (fraction of frame width); full height is kept for bodies above feet

DASHBOARD_HOST = "127.0.0.1"
DASHBOARD_PORT = 5000
DASHBOARD_DEBUG = os.environ.get("DASHBOARD_DEBUG", "false").lower() == "true"

DASHBOARD_USERNAME = os.environ.get("DASHBOARD_USERNAME", "admin")
DASHBOARD_PASSWORD_HASH = os.environ.get("DASHBOARD_PASSWORD_HASH", "")
FLASK_SECRET_KEY = os.environ.get("FLASK_SECRET_KEY", "")

LOGIN_MAX_ATTEMPTS = 5
LOGIN_LOCKOUT_WINDOW_SECONDS = 15 * 60
SESSION_LIFETIME_HOURS = 12

LOG_MAX_BYTES = 5 * 1024 * 1024
LOG_BACKUP_COUNT = 5
RECONNECT_AFTER_FAILURES = 5
RECONNECT_BACKOFF_SECONDS = 5

SNAPSHOT_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "snapshots")
SNAPSHOT_RETENTION_SECONDS = 3600
SNAPSHOT_BLUR_FACES = os.environ.get("SNAPSHOT_BLUR_FACES", "true").lower() == "true"
FACE_BLUR_FRACTION = 0.25
