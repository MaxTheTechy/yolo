import math

import cv2
import numpy as np

import config
import database


class VisitTracker:
    """Turns ByteTrack ids inside one camera's zone into visits (in/out events).

    - A person counts only after the room's min_dwell_seconds in the zone; the visit is then
      written with entered_at = first time they were seen (so short stays like a
      parcel drop never appear, but real stays count from their true start).
    - A person who disappears for up to EXIT_GRACE_SECONDS is still counted
      (occlusion); after that the visit closes at the last time they were seen.
    - ByteTrack sometimes gives a person a new id after occlusion; a new id that
      appears close to where a recently lost one was inherits its state.
    """

    def __init__(self, room_id, camera_id, zone_points=None, min_dwell_seconds=None):
        self.room_id = room_id
        self.min_dwell_seconds = config.MIN_DWELL_SECONDS if min_dwell_seconds is None else min_dwell_seconds
        self.camera_id = camera_id
        self.zone = np.array(zone_points, dtype=np.float32) if zone_points and len(zone_points) >= 3 else None
        self.tracks = {}  # tracker_id -> {"first_seen", "last_seen", "pos", "visit_id"}

    def in_zone(self, x, y):
        if self.zone is None:
            return True
        return cv2.pointPolygonTest(self.zone, (float(x), float(y)), False) >= 0

    def update(self, detections, frame_w, frame_h, now):
        """detections: sv.Detections with tracker_id. Returns confirmed people currently in the zone."""
        seen = set()
        # ids visible in this frame are never re-linked to someone else (they are still here)
        visible = set() if detections.tracker_id is None else {int(t) for t in detections.tracker_id}
        if detections.tracker_id is not None:
            for (x1, y1, x2, y2), tid in zip(detections.xyxy, detections.tracker_id):
                # feet position, normalised to 0..1 like the stored zone
                px, py = ((x1 + x2) / 2) / frame_w, y2 / frame_h
                if not self.in_zone(px, py):
                    continue
                tid = int(tid)
                seen.add(tid)
                track = self.tracks.get(tid) or self._relink(px, py, now, exclude=visible)
                if track is None:
                    track = {"first_seen": now, "visit_id": None}
                track.update(last_seen=now, pos=(px, py))
                self.tracks[tid] = track

        for tid, track in list(self.tracks.items()):
            if tid not in seen and (now - track["last_seen"]).total_seconds() > config.EXIT_GRACE_SECONDS:
                if track["visit_id"]:
                    database.close_visit(track["visit_id"], track["last_seen"])
                del self.tracks[tid]
                continue
            if track["visit_id"] is None and (track["last_seen"] - track["first_seen"]).total_seconds() >= self.min_dwell_seconds:
                track["visit_id"] = database.open_visit(self.room_id, self.camera_id, track["first_seen"], track["last_seen"])

        return sum(1 for t in self.tracks.values() if t["visit_id"])

    def _relink(self, px, py, now, exclude):
        best_tid, best_dist = None, config.REACQUIRE_DISTANCE
        for tid, track in self.tracks.items():
            if tid in exclude or track["last_seen"] == now:
                continue
            dist = math.dist((px, py), track["pos"]) / math.sqrt(2)
            if dist <= best_dist:
                best_tid, best_dist = tid, dist
        return self.tracks.pop(best_tid) if best_tid is not None else None

    def open_visit_ids(self):
        return [t["visit_id"] for t in self.tracks.values() if t["visit_id"]]

    def close_all(self):
        for track in self.tracks.values():
            if track["visit_id"]:
                database.close_visit(track["visit_id"], track["last_seen"])
        self.tracks.clear()


class LineCounter:
    """Counts people crossing an entrance line, using ByteTrack ids.

    - line = {"a": [x, y], "b": [x, y], "inside": [x, y]} (normalised 0..1); "inside" is any point
      on the room side of the line.
    - A person's side is only updated when their feet are more than LINE_HYSTERESIS past the line,
      so jitter on the line never counts; "inside" also requires being level with the doorway.
    - Journey counting (like dedicated overhead counters): each person has an origin side (where
      their journey started or last counted). A crossing counts only once they have stayed on the
      other side for JOURNEY_SETTLE_SECONDS, or vanished there (e.g. through the door); the event
      is timestamped when they crossed. Stepping across and back, or hovering on the line, counts
      nothing; crossing back and forth several times counts only the net result.
      outside -> inside = in (+1), inside -> outside = out (-1). Someone first seen inside who
      walks out counts as an exit.
    - Like VisitTracker, a new ByteTrack id near a recently lost person inherits their journey.
    """

    def __init__(self, room_id, camera_id, line):
        self.room_id = room_id
        self.camera_id = camera_id
        self.a = np.array(line["a"], dtype=float)
        self.b = np.array(line["b"], dtype=float)
        self.inside_sign = 1 if self._signed(np.array(line["inside"], dtype=float), 1, 1) >= 0 else -1
        self.tracks = {}  # tracker_id -> {"origin", "side", "changed_at", "pos", "last_seen"}

    def _signed(self, p, w, h):
        """Signed distance of p from the line in pixels (frame w x h)."""
        a, b, p = self.a * (w, h), self.b * (w, h), p * (w, h)
        d = b - a
        return float(d[0] * (p[1] - a[1]) - d[1] * (p[0] - a[0])) / (np.hypot(*d) or 1)

    def side(self, px, py, w, h):
        """+1 inside, -1 outside, None if on/near the line.

        Outside counts anywhere on the outer side (people often approach from beyond the ends of the
        line); inside only counts level with the doorway, so walking past the end of the line and
        round it never registers as entering."""
        p = np.array((px, py))
        dist = self._signed(p, w, h)
        if abs(dist) < config.LINE_HYSTERESIS * math.hypot(w, h):
            return None
        if (dist > 0) != (self.inside_sign > 0):
            return -1
        a, b = self.a * (w, h), self.b * (w, h)
        d = b - a
        t = float(np.dot(p * (w, h) - a, d) / (np.dot(d, d) or 1))
        return 1 if -0.25 <= t <= 1.25 else None

    def update(self, detections, frame_w, frame_h, now):
        """Returns a list of directions (+1 in / -1 out) that happened in this frame."""
        events, seen = [], set()
        visible = set() if detections.tracker_id is None else {int(t) for t in detections.tracker_id}
        if detections.tracker_id is not None:
            for (x1, y1, x2, y2), tid in zip(detections.xyxy, detections.tracker_id):
                px, py = ((x1 + x2) / 2) / frame_w, y2 / frame_h
                tid = int(tid)
                seen.add(tid)
                track = (self.tracks.get(tid) or self._relink(px, py, now, exclude=visible)
                         or {"origin": None, "side": None, "changed_at": None})
                side = self.side(px, py, frame_w, frame_h)
                if side is not None:
                    if track["origin"] is None:
                        track["origin"] = track["side"] = side
                    elif side != track["side"]:
                        track["side"], track["changed_at"] = side, now
                track.update(pos=(px, py), last_seen=now)
                self.tracks[tid] = track
        for tid, track in list(self.tracks.items()):
            # settled on the other side (still visible, or gone from view there) -> one event
            if (track["origin"] is not None and track["side"] != track["origin"]
                    and (now - track["changed_at"]).total_seconds() >= config.JOURNEY_SETTLE_SECONDS):
                database.record_door_event(self.room_id, self.camera_id, track["changed_at"], track["side"])
                events.append(track["side"])
                track["origin"] = track["side"]
            if tid not in seen and (now - track["last_seen"]).total_seconds() > config.EXIT_GRACE_SECONDS:
                del self.tracks[tid]
        return events

    def _relink(self, px, py, now, exclude):
        best_tid, best_dist = None, config.REACQUIRE_DISTANCE
        for tid, track in self.tracks.items():
            if tid in exclude or track["last_seen"] == now:
                continue
            dist = math.dist((px, py), track["pos"]) / math.sqrt(2)
            if dist <= best_dist:
                best_tid, best_dist = tid, dist
        return self.tracks.pop(best_tid) if best_tid is not None else None
