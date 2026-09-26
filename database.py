from datetime import datetime, timedelta, timezone

import json

from sqlalchemy import create_engine, Column, String, DateTime, Integer, Numeric, Float, Boolean, Text, ForeignKey, func, inspect, text
from sqlalchemy.orm import declarative_base, sessionmaker

import config

Base = declarative_base()


class RoomOccupancy(Base):
    __tablename__ = "room_occupancy"

    id = Column(Integer, primary_key=True, autoincrement=True)
    room_name = Column(String(50), nullable=False)
    timestamp = Column(DateTime, nullable=False)
    people_count = Column(Integer, nullable=False)
    occupancy_percent = Column(Numeric(5, 2), nullable=False)
    snapshot_path = Column(String(255), nullable=True)


class Room(Base):
    __tablename__ = "room"

    id = Column(Integer, primary_key=True, autoincrement=True)
    name = Column(String(50), nullable=False, unique=True)
    capacity = Column(Integer, nullable=False)
    open_time = Column(String(5), nullable=False, default="07:00")
    close_time = Column(String(5), nullable=False, default="17:00")
    min_dwell_seconds = Column(Integer, nullable=False, default=config.MIN_DWELL_SECONDS)  # shorter stays are ignored
    door_count = Column(Integer, nullable=False, default=0)  # rooms with an entrance camera: ins - outs, never below 0


class Camera(Base):
    __tablename__ = "camera"

    id = Column(Integer, primary_key=True, autoincrement=True)
    room_id = Column(Integer, ForeignKey("room.id"), nullable=False)
    name = Column(String(50), nullable=False)
    url = Column(String(500), nullable=False)
    mode = Column(String(10), nullable=False, default="zone")  # "zone" (count people in view) or "entrance" (count line crossings)
    zone = Column(Text, nullable=True)  # JSON [[x, y], ...] normalised 0..1; NULL = whole frame
    line = Column(Text, nullable=True)  # entrance mode: JSON {"a": [x, y], "b": [x, y], "inside": [x, y]} normalised 0..1
    frame_interval = Column(Float, nullable=True)  # seconds between analysed frames; NULL = TRACK_FPS / ENTRANCE_TRACK_FPS
    enabled = Column(Boolean, nullable=False, default=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow)
    last_seen = Column(DateTime, nullable=True)
    last_error = Column(String(255), nullable=True)

    @property
    def zone_points(self):
        return json.loads(self.zone) if self.zone else None

    @property
    def line_def(self):
        return json.loads(self.line) if self.line else None


class Visit(Base):
    """One person's stay inside a camera zone. Only written once they pass MIN_DWELL_SECONDS."""
    __tablename__ = "visit"

    id = Column(Integer, primary_key=True, autoincrement=True)
    room_id = Column(Integer, ForeignKey("room.id"), nullable=False, index=True)
    camera_id = Column(Integer, ForeignKey("camera.id"), nullable=False)
    entered_at = Column(DateTime, nullable=False, index=True)
    last_seen_at = Column(DateTime, nullable=False)
    exited_at = Column(DateTime, nullable=True, index=True)


class DoorEvent(Base):
    """One person crossing an entrance line: direction +1 = in, -1 = out."""
    __tablename__ = "door_event"

    id = Column(Integer, primary_key=True, autoincrement=True)
    room_id = Column(Integer, ForeignKey("room.id"), nullable=False, index=True)
    camera_id = Column(Integer, ForeignKey("camera.id"), nullable=False)
    timestamp = Column(DateTime, nullable=False, index=True)
    direction = Column(Integer, nullable=False)
    count_after = Column(Integer, nullable=False)  # room head-count after this event (clamped at 0)


class LoginAttempt(Base):
    __tablename__ = "login_attempt"

    id = Column(Integer, primary_key=True, autoincrement=True)
    ip = Column(String(45), nullable=False)
    username = Column(String(100), nullable=True)
    success = Column(Integer, nullable=False)
    timestamp = Column(DateTime, nullable=False)


_engine = create_engine(config.DATABASE_URL)
_SessionLocal = sessionmaker(bind=_engine)


def init_db():
    Base.metadata.create_all(_engine)
    inspector = inspect(_engine)
    columns = {c["name"] for c in inspector.get_columns("room_occupancy")}
    if "snapshot_path" not in columns:
        with _engine.begin() as conn:
            conn.execute(text("ALTER TABLE room_occupancy ADD COLUMN snapshot_path VARCHAR(255)"))
    room_cols = {c["name"] for c in inspector.get_columns("room")}
    camera_cols = {c["name"] for c in inspector.get_columns("camera")}
    with _engine.begin() as conn:
        if "min_dwell_seconds" not in room_cols:
            conn.execute(text(f"ALTER TABLE room ADD COLUMN min_dwell_seconds INTEGER NOT NULL DEFAULT {int(config.MIN_DWELL_SECONDS)}"))
        if "door_count" not in room_cols:
            conn.execute(text("ALTER TABLE room ADD COLUMN door_count INTEGER NOT NULL DEFAULT 0"))
        if "mode" not in camera_cols:
            conn.execute(text("ALTER TABLE camera ADD COLUMN mode VARCHAR(10) NOT NULL DEFAULT 'zone'"))
        if "line" not in camera_cols:
            conn.execute(text("ALTER TABLE camera ADD COLUMN line TEXT"))
        if "frame_interval" not in camera_cols:
            conn.execute(text("ALTER TABLE camera ADD COLUMN frame_interval FLOAT"))
    _seed_default_room()
    _close_orphaned_visits()


def _seed_default_room():
    """First run after the multi-room upgrade: turn the old single-room config into DB rows."""
    session = get_session()
    try:
        if session.query(Room).count():
            return
        room = Room(name=config.ROOM_NAME, capacity=config.ROOM_CAPACITY)
        session.add(room)
        session.flush()
        session.add(Camera(room_id=room.id, name="Camera 1", url=config.RTSP_URL))
        session.commit()
    finally:
        session.close()


def _close_orphaned_visits():
    """Visits left open by a crash/restart end at the last time the person was seen."""
    with _engine.begin() as conn:
        conn.execute(text("UPDATE visit SET exited_at = last_seen_at WHERE exited_at IS NULL"))


def get_session():
    return _SessionLocal()


def record_occupancy(room_name, people_count, occupancy_percent, timestamp=None, snapshot_path=None):
    session = get_session()
    try:
        record = RoomOccupancy(
            room_name=room_name,
            timestamp=timestamp or datetime.utcnow(),
            people_count=people_count,
            occupancy_percent=occupancy_percent,
            snapshot_path=snapshot_path,
        )
        session.add(record)
        session.commit()
        return record.id
    finally:
        session.close()


def get_latest(room_name):
    session = get_session()
    try:
        return (
            session.query(RoomOccupancy)
            .filter(RoomOccupancy.room_name == room_name)
            .order_by(RoomOccupancy.timestamp.desc())
            .first()
        )
    finally:
        session.close()


def get_history(room_name, since=None):
    session = get_session()
    try:
        query = session.query(RoomOccupancy).filter(RoomOccupancy.room_name == room_name)
        if since:
            query = query.filter(RoomOccupancy.timestamp >= since)
        return query.order_by(RoomOccupancy.timestamp.asc()).all()
    finally:
        session.close()


def record_login_attempt(ip, username, success, timestamp=None):
    session = get_session()
    try:
        attempt = LoginAttempt(
            ip=ip,
            username=username,
            success=1 if success else 0,
            timestamp=timestamp or datetime.utcnow(),
        )
        session.add(attempt)
        session.commit()
    finally:
        session.close()


def count_recent_login_failures(ip, since):
    session = get_session()
    try:
        return (
            session.query(LoginAttempt)
            .filter(LoginAttempt.ip == ip)
            .filter(LoginAttempt.success == 0)
            .filter(LoginAttempt.timestamp >= since)
            .count()
        )
    finally:
        session.close()


def get_peak(room_name, since=None):
    """Record with the highest people_count (ties broken by earliest timestamp)."""
    session = get_session()
    try:
        query = session.query(RoomOccupancy).filter(RoomOccupancy.room_name == room_name)
        if since:
            query = query.filter(RoomOccupancy.timestamp >= since)
        return query.order_by(RoomOccupancy.people_count.desc(), RoomOccupancy.timestamp.asc()).first()
    finally:
        session.close()


def _local_groups(room_name, start, end, tz, key_fmt):
    """Samples in [start, end) (naive UTC) grouped by their local-time strftime(key_fmt)."""
    session = get_session()
    try:
        rows = (session.query(RoomOccupancy.timestamp, RoomOccupancy.people_count, RoomOccupancy.occupancy_percent)
                .filter(RoomOccupancy.room_name == room_name)
                .filter(RoomOccupancy.timestamp >= start, RoomOccupancy.timestamp < end)
                .order_by(RoomOccupancy.timestamp).all())
    finally:
        session.close()
    groups = {}
    for ts, people, pct in rows:
        key = ts.replace(tzinfo=timezone.utc).astimezone(tz).strftime(key_fmt)
        groups.setdefault(key, []).append((people, float(pct)))
    return groups


def get_hourly_stats(room_name, day_start, day_end, tz):
    """Per local-hour aggregates for samples in [day_start, day_end) (naive UTC bounds of a local day)."""
    return [
        {
            "hour": int(hour),
            "avg_people": round(sum(p for p, _ in g) / len(g), 2),
            "max_people": max(p for p, _ in g),
            "avg_occupancy": round(sum(o for _, o in g) / len(g), 2),
            "samples": len(g),
        }
        for hour, g in sorted(_local_groups(room_name, day_start, day_end, tz, "%H").items())
    ]


def get_daily_stats(room_name, since, tz):
    """Per local-day aggregates for samples since `since` (naive UTC)."""
    return [
        {
            "day": day,
            "avg_people": round(sum(p for p, _ in g) / len(g), 2),
            "max_people": max(p for p, _ in g),
            "peak_occupancy": round(max(o for _, o in g), 2),
            "samples": len(g),
        }
        for day, g in sorted(_local_groups(room_name, since, datetime.utcnow() + timedelta(minutes=1), tz, "%Y-%m-%d").items())
    ]


# --- rooms / cameras -------------------------------------------------------

def _detached(query_fn):
    session = get_session()
    try:
        result = query_fn(session)
        session.expunge_all()
        return result
    finally:
        session.close()


def list_rooms():
    return _detached(lambda s: s.query(Room).order_by(Room.name).all())


def get_room(room_id):
    return _detached(lambda s: s.get(Room, room_id))


def get_room_by_name(name):
    return _detached(lambda s: s.query(Room).filter(Room.name == name).first())


def list_cameras(room_id=None, enabled_only=False):
    def q(s):
        query = s.query(Camera)
        if room_id is not None:
            query = query.filter(Camera.room_id == room_id)
        if enabled_only:
            query = query.filter(Camera.enabled.is_(True))
        return query.order_by(Camera.room_id, Camera.id).all()
    return _detached(q)


def get_camera(camera_id):
    return _detached(lambda s: s.get(Camera, camera_id))


def save_room(room_id, **fields):
    session = get_session()
    try:
        room = session.get(Room, room_id) if room_id else Room()
        for k, v in fields.items():
            setattr(room, k, v)
        session.add(room)
        session.commit()
        return room.id
    finally:
        session.close()


def delete_room(room_id):
    session = get_session()
    try:
        if session.query(Camera).filter(Camera.room_id == room_id).count():
            raise ValueError("Remove the room's cameras first")
        session.query(DoorEvent).filter(DoorEvent.room_id == room_id).delete()
        session.query(Room).filter(Room.id == room_id).delete()
        session.commit()
    finally:
        session.close()


def save_camera(camera_id, **fields):
    session = get_session()
    try:
        camera = session.get(Camera, camera_id) if camera_id else Camera()
        if not camera_id or fields.get("room_id", camera.room_id) != camera.room_id:
            room_id = fields.get("room_id", camera.room_id)
            others = session.query(Camera).filter(Camera.room_id == room_id, Camera.id != (camera_id or 0)).count()
            if others >= config.MAX_CAMERAS_PER_ROOM:
                raise ValueError(f"A room can have at most {config.MAX_CAMERAS_PER_ROOM} cameras")
        for k, v in fields.items():
            setattr(camera, k, v)
        camera.updated_at = datetime.utcnow()
        session.add(camera)
        session.commit()
        return camera.id
    finally:
        session.close()


def delete_camera(camera_id):
    session = get_session()
    try:
        session.query(Camera).filter(Camera.id == camera_id).delete()
        session.commit()
    finally:
        session.close()


def update_camera_status(camera_id, last_seen=None, last_error=None):
    with _engine.begin() as conn:
        conn.execute(
            text("UPDATE camera SET last_seen = COALESCE(:seen, last_seen), last_error = :err WHERE id = :id"),
            {"seen": last_seen, "err": last_error, "id": camera_id},
        )


# --- visits ----------------------------------------------------------------

def open_visit(room_id, camera_id, entered_at, last_seen_at):
    session = get_session()
    try:
        visit = Visit(room_id=room_id, camera_id=camera_id, entered_at=entered_at, last_seen_at=last_seen_at)
        session.add(visit)
        session.commit()
        return visit.id
    finally:
        session.close()


def touch_visits(visit_ids, last_seen_at):
    if not visit_ids:
        return
    session = get_session()
    try:
        session.query(Visit).filter(Visit.id.in_(visit_ids)).update(
            {Visit.last_seen_at: last_seen_at}, synchronize_session=False
        )
        session.commit()
    finally:
        session.close()


def close_visit(visit_id, exited_at):
    session = get_session()
    try:
        session.query(Visit).filter(Visit.id == visit_id).update(
            {Visit.exited_at: exited_at, Visit.last_seen_at: exited_at}, synchronize_session=False
        )
        session.commit()
    finally:
        session.close()


def get_visits(room_id, start, end):
    """Visits overlapping [start, end). Open visits are returned with exited_at=None."""
    return _detached(lambda s: s.query(Visit)
                     .filter(Visit.room_id == room_id)
                     .filter(Visit.entered_at < end)
                     .filter((Visit.exited_at.is_(None)) | (Visit.exited_at > start))
                     .order_by(Visit.entered_at).all())


# --- entrance (door) events ------------------------------------------------

def record_door_event(room_id, camera_id, timestamp, direction):
    """Apply one crossing to the room's running count (never below 0 unless DOOR_COUNT_ALLOW_NEGATIVE) and log it.
    Returns the new count."""
    new_count = "door_count + :d" if config.DOOR_COUNT_ALLOW_NEGATIVE else "MAX(0, door_count + :d)"
    with _engine.begin() as conn:
        conn.execute(text(f"UPDATE room SET door_count = {new_count} WHERE id = :id"),
                     {"d": direction, "id": room_id})
        count = conn.execute(text("SELECT door_count FROM room WHERE id = :id"), {"id": room_id}).scalar()
        conn.execute(text("INSERT INTO door_event (room_id, camera_id, timestamp, direction, count_after) "
                          "VALUES (:r, :c, :t, :d, :n)"),
                     {"r": room_id, "c": camera_id, "t": timestamp, "d": direction, "n": count})
    return count


def get_door_count(room_id):
    with _engine.connect() as conn:
        return conn.execute(text("SELECT door_count FROM room WHERE id = :id"), {"id": room_id}).scalar() or 0


def get_door_events(room_id, start, end):
    return _detached(lambda s: s.query(DoorEvent)
                     .filter(DoorEvent.room_id == room_id)
                     .filter(DoorEvent.timestamp >= start, DoorEvent.timestamp < end)
                     .order_by(DoorEvent.timestamp, DoorEvent.id).all())


def get_door_count_at(room_id, when):
    """Head-count just before `when` (0 if no earlier event)."""
    with _engine.connect() as conn:
        return conn.execute(text("SELECT count_after FROM door_event WHERE room_id = :r AND timestamp < :t "
                                 "ORDER BY timestamp DESC, id DESC LIMIT 1"), {"r": room_id, "t": when}).scalar() or 0


def get_in_out_times(room_id, start, end):
    """(in_times, out_times) in [start, end): entrance rooms from line crossings, others from visits."""
    if room_has_entrance(room_id):
        events = get_door_events(room_id, start, end)
        return [e.timestamp for e in events if e.direction > 0], [e.timestamp for e in events if e.direction < 0]
    visits = get_visits(room_id, start, end)
    return ([v.entered_at for v in visits if v.entered_at >= start],
            [v.exited_at for v in visits if v.exited_at and start <= v.exited_at < end])


def room_has_entrance(room_id):
    return any(c.mode == "entrance" for c in list_cameras(room_id))


def get_sample_times(room_name, start, end):
    session = get_session()
    try:
        return [r[0] for r in session.query(RoomOccupancy.timestamp)
                .filter(RoomOccupancy.room_name == room_name)
                .filter(RoomOccupancy.timestamp >= start, RoomOccupancy.timestamp < end)
                .order_by(RoomOccupancy.timestamp).all()]
    finally:
        session.close()
