import logging
import os
import signal
import threading
import time
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler

import cv2

import config
import database
import occupancy
import snapshots
from detector import PersonDetector
from source import is_youtube_url, resolve_stream_url
from tracker import LineCounter, VisitTracker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(threadName)s %(message)s",
    handlers=[
        logging.StreamHandler(),
        RotatingFileHandler(
            "logs/app.log",
            maxBytes=config.LOG_MAX_BYTES,
            backupCount=config.LOG_BACKUP_COUNT,
        ),
    ],
)
log = logging.getLogger(__name__)


def preview_filename(camera_id):
    return f"camera{camera_id}_latest.jpg"


def is_local_file(url):
    return os.path.isfile(url)


class CameraWorker(threading.Thread):
    """Reads one camera, runs YOLO + ByteTrack at TRACK_FPS, maintains visits for its zone."""

    def __init__(self, camera, room):
        super().__init__(name=f"cam{camera.id}", daemon=True)
        self.camera = camera
        self.room = room
        self.signature = (camera.url, camera.zone, camera.room_id, camera.updated_at)
        self.stop_event = threading.Event()
        self.count = 0
        self.last_frame_at = None
        self.snapshot_path = None  # set when the count changes; consumed by the room sampler
        self.detections = []

    def online(self):
        return self.last_frame_at and time.time() - self.last_frame_at < config.CAMERA_OFFLINE_SECONDS

    def open_capture(self):
        try:
            cap = cv2.VideoCapture(resolve_stream_url(self.camera.url))
        except Exception as exc:
            log.exception("Failed to resolve stream URL")
            database.update_camera_status(self.camera.id, last_error=str(exc)[:255])
            return None
        if not cap.isOpened():
            cap.release()
            database.update_camera_status(self.camera.id, last_error="Could not open stream")
            return None
        return cap

    def run(self):
        import supervision as sv

        detector = PersonDetector()
        entrance = self.camera.mode == "entrance"
        fps = config.ENTRANCE_TRACK_FPS if entrance else config.TRACK_FPS
        # supervision keeps lost tracks for int(frame_rate / 30 * lost_track_buffer) frames,
        # so the buffer is given in 30fps units to get EXIT_GRACE_SECONDS at any fps
        byte_track = sv.ByteTrack(frame_rate=fps,
                                  lost_track_buffer=int(config.EXIT_GRACE_SECONDS * 30))
        if entrance and not self.camera.line_def:
            log.warning("Entrance camera %s has no line yet - draw it on the admin page", self.camera.name)
        line_counter = LineCounter(self.room.id, self.camera.id, self.camera.line_def) if entrance and self.camera.line_def else None
        visits = VisitTracker(self.room.id, self.camera.id, self.camera.zone_points, self.room.min_dwell_seconds)
        local_file = is_local_file(self.camera.url)
        # YouTube live (HLS) arrives in ~5s bursts: pick frames by video position, not wall-clock,
        # or most of each burst is skipped and people cross the line unseen
        bursty = is_youtube_url(self.camera.url)
        frame_no = 0
        interval = 1.0 / fps
        cap, failures, last_status, last_processed = None, 0, 0, 0
        try:
            while not self.stop_event.is_set():
                if cap is None:
                    cap = self.open_capture()
                    if cap is None:
                        log.error("Camera %s unavailable, retrying in %ds", self.camera.name, config.RECONNECT_BACKOFF_SECONDS)
                        self.stop_event.wait(config.RECONNECT_BACKOFF_SECONDS)
                        continue
                    log.info("Connected to camera %s (room %s)", self.camera.name, self.room.name)
                    file_skip = max(1, round((cap.get(cv2.CAP_PROP_FPS) or 25) / fps)) if local_file or bursty else 1

                if local_file:
                    # test videos: play back in real time and loop at the end
                    self.stop_event.wait(max(0, interval - (time.time() - last_processed)))
                    for _ in range(file_skip - 1):
                        cap.grab()
                    ok, frame = cap.read()
                    if not ok:
                        cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        continue
                else:
                    # live streams: keep draining the buffer, decode only what we process
                    if not cap.grab():
                        failures += 1
                        if failures >= config.RECONNECT_AFTER_FAILURES:
                            log.warning("Camera %s: too many read failures, reconnecting", self.camera.name)
                            cap.release()
                            cap, failures = None, 0
                        else:
                            time.sleep(1)
                        continue
                    failures = 0
                    if bursty:
                        frame_no += 1
                        if frame_no % file_skip:
                            continue
                    elif time.time() - last_processed < interval:
                        continue
                    ok, frame = cap.retrieve()
                    if not ok:
                        continue

                last_processed = time.time()
                self.last_frame_at = last_processed
                now = datetime.utcnow()
                try:
                    h, w = frame.shape[:2]
                    roi = line_roi(self.camera.line_def, w, h) if entrance else zone_roi(self.camera.zone_points, w, h)
                    imgsz = config.ENTRANCE_IMGSZ if entrance else config.YOLO_IMGSZ
                    detections = byte_track.update_with_detections(detector.detect_sv(frame, roi, imgsz))
                    self.detections = [(*box, 0.0) for box in detections.xyxy.tolist()]
                    if entrance:
                        events = line_counter.update(detections, w, h, now) if line_counter else []
                        for direction in events:
                            log.info("Entrance %s: %s", self.camera.name, "IN" if direction > 0 else "OUT")
                        count = self.count + len(events)  # any event = new snapshot
                    else:
                        visits.min_dwell_seconds = self.room.min_dwell_seconds  # admin edits apply live
                        count = visits.update(detections, w, h, now)
                    if count != self.count:
                        self.snapshot_path = snapshots.save_snapshot(
                            frame, f"{self.room.name}_cam{self.camera.id}", now, detections=self.detections)
                        self.count = count
                    if last_processed - last_status >= config.SAMPLE_INTERVAL_SECONDS:
                        last_status = last_processed
                        database.touch_visits(visits.open_visit_ids(), now)
                        database.update_camera_status(self.camera.id, last_seen=now, last_error=None)
                        preview = snapshots.blur_face_regions(frame, self.detections) if config.SNAPSHOT_BLUR_FACES else frame
                        cv2.imwrite(os.path.join(config.SNAPSHOT_DIR, preview_filename(self.camera.id)), preview)
                except Exception:
                    log.exception("Error processing frame on camera %s", self.camera.name)
        finally:
            visits.close_all()
            if cap is not None:
                cap.release()
            log.info("Camera %s stopped", self.camera.name)


class Supervisor:
    """Starts/stops camera workers as the admin page changes the DB, writes room samples."""

    def __init__(self):
        self.workers = {}
        self.stop_event = threading.Event()

    def sync_workers(self):
        rooms = {r.id: r for r in database.list_rooms()}
        wanted = {c.id: c for c in database.list_cameras(enabled_only=True) if c.room_id in rooms}
        for cam_id, worker in list(self.workers.items()):
            cam = wanted.get(cam_id)
            if cam is None or worker.signature != (cam.url, cam.zone, cam.room_id, cam.updated_at):
                log.info("Stopping camera %s (removed or changed)", worker.camera.name)
                worker.stop_event.set()
                worker.join(timeout=30)
                del self.workers[cam_id]
        for cam_id, cam in wanted.items():
            if cam_id not in self.workers:
                worker = CameraWorker(cam, rooms[cam.room_id])
                worker.start()
                self.workers[cam_id] = worker
        # keep room objects fresh (capacity/name edits)
        for worker in self.workers.values():
            worker.room = rooms[worker.camera.room_id]

    def sample_rooms(self):
        by_room = {}
        for worker in self.workers.values():
            by_room.setdefault(worker.camera.room_id, []).append(worker)
        for workers in by_room.values():
            room = workers[0].room
            counted = [w for w in workers if w.camera.mode == "entrance"] or workers
            if not all(w.online() for w in counted):
                log.warning("Room %s: camera offline, sample skipped", room.name)
                continue
            # a room with an entrance camera is counted by the door (ins - outs); other cameras are ignored
            if counted is not workers:
                people = database.get_door_count(room.id)
            else:
                # each camera watches its own area of the room, so counts add up
                people = sum(w.count for w in workers)
            snapshot = next((w.snapshot_path for w in workers if w.snapshot_path), None)
            for w in workers:
                w.snapshot_path = None
            pct = occupancy.record(room, people, snapshot_path=snapshot)
            log.info("room=%s people=%d occupancy=%.2f%%", room.name, people, pct)

    def run(self):
        database.init_db()
        os.makedirs(config.SNAPSHOT_DIR, exist_ok=True)
        last_sync = last_sample = 0
        while not self.stop_event.is_set():
            now = time.time()
            try:
                if now - last_sync >= config.CONFIG_POLL_SECONDS:
                    last_sync = now
                    self.sync_workers()
                if now - last_sample >= config.SAMPLE_INTERVAL_SECONDS:
                    last_sample = now
                    self.sample_rooms()
                    snapshots.purge_old_snapshots()
            except Exception:
                log.exception("Supervisor loop error")
            self.stop_event.wait(1)
        for worker in self.workers.values():
            worker.stop_event.set()
        for worker in self.workers.values():
            worker.join(timeout=30)


def line_roi(line, w, h):
    """Box around the entrance line and its inside marker, full height (people are taller than the line)."""
    if not line:
        return None
    return zone_roi([line["a"], line["b"], line["inside"]], w, h)


def zone_roi(zone_points, w, h):
    """Pixel box around the zone's x-range (plus margin), full height: feet are in the zone, heads may be above it."""
    if not zone_points:
        return None
    xs = [p[0] for p in zone_points]
    x1 = max(0, int((min(xs) - config.ZONE_CROP_MARGIN) * w))
    x2 = min(w, int((max(xs) + config.ZONE_CROP_MARGIN) * w))
    return None if x2 - x1 >= w * 0.9 else (x1, 0, x2, h)


def main():
    import torch

    torch.set_num_threads(config.TORCH_THREADS)
    supervisor = Supervisor()
    signal.signal(signal.SIGTERM, lambda *_: supervisor.stop_event.set())
    try:
        supervisor.run()
    except KeyboardInterrupt:
        supervisor.stop_event.set()
    log.info("Shut down")


if __name__ == "__main__":
    main()
