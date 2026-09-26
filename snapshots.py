import os
import time

import cv2

import config


def blur_face_regions(frame, detections):
    """Pixelate the top fraction of each person box (approximate head/face area)."""
    blurred = frame.copy()
    h, w = blurred.shape[:2]
    for (x1, y1, x2, y2, _conf) in detections:
        x1, y1 = max(0, int(x1)), max(0, int(y1))
        x2, y2 = min(w, int(x2)), min(h, int(y2))
        if x2 <= x1 or y2 <= y1:
            continue

        face_y2 = max(y1 + 1, y1 + int((y2 - y1) * config.FACE_BLUR_FRACTION))
        region = blurred[y1:face_y2, x1:x2]
        if region.size == 0:
            continue

        small_w = max(1, region.shape[1] // 8)
        small_h = max(1, region.shape[0] // 8)
        small = cv2.resize(region, (small_w, small_h), interpolation=cv2.INTER_LINEAR)
        pixelated = cv2.resize(small, (region.shape[1], region.shape[0]), interpolation=cv2.INTER_NEAREST)
        blurred[y1:face_y2, x1:x2] = pixelated

    return blurred


def save_snapshot(frame, room_name, timestamp, detections=None):
    if detections is not None and config.SNAPSHOT_BLUR_FACES:
        frame = blur_face_regions(frame, detections)
    os.makedirs(config.SNAPSHOT_DIR, exist_ok=True)
    filename = f"{room_name}_{timestamp.strftime('%Y%m%d_%H%M%S_%f')}.jpg"
    path = os.path.join(config.SNAPSHOT_DIR, filename)
    cv2.imwrite(path, frame)
    return filename


def purge_old_snapshots(max_age_seconds=None):
    max_age_seconds = max_age_seconds or config.SNAPSHOT_RETENTION_SECONDS
    if not os.path.isdir(config.SNAPSHOT_DIR):
        return
    cutoff = time.time() - max_age_seconds
    for name in os.listdir(config.SNAPSHOT_DIR):
        path = os.path.join(config.SNAPSHOT_DIR, name)
        if os.path.isfile(path) and os.path.getmtime(path) < cutoff:
            os.remove(path)
