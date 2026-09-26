"""Daily occupancy reports, computed from visits (in/out events), not raw samples.
Rooms with an entrance camera use its line crossings (door_event) instead.

Because visits already exclude stays shorter than MIN_DWELL_SECONDS, a parcel
delivery never shows up as a peak.
"""
import csv
import io
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

import config
import database

TZ = ZoneInfo(config.TIMEZONE)

CSV_COLUMNS = [
    ("date", "Date"), ("room", "Room"), ("window", "Window"), ("coverage_pct", "Data coverage %"),
    ("entries", "Entries"), ("exits", "Exits"), ("max_people", "Max people"), ("min_people", "Min people"),
    ("avg_people", "Avg people"), ("avg_occupancy_pct", "Avg occupancy %"), ("peak_occupancy_pct", "Peak occupancy %"),
    ("peak_start", "Peak start"), ("peak_end", "Peak end"), ("peak_minutes", "Peak duration (min)"),
    ("minutes_at_peak", "Total time at peak (min)"), ("occupied_minutes", "Occupied time (min)"),
]


def local_to_utc(day, hhmm):
    local = datetime.combine(day, time.fromisoformat(hhmm), tzinfo=TZ)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def utc_to_local_hhmm(dt):
    return dt.replace(tzinfo=timezone.utc).astimezone(TZ).strftime("%H:%M")


def day_report(room, day, open_time=None, close_time=None):
    open_time, close_time = open_time or room.open_time, close_time or room.close_time
    start, end = local_to_utc(day, open_time), local_to_utc(day, close_time)
    now = datetime.utcnow()
    row = {"date": day.isoformat(), "room": room.name, "window": f"{open_time}-{close_time}"}
    if start >= now:
        return None
    end = min(end, now)
    window_s = (end - start).total_seconds()

    if database.room_has_entrance(room.id):
        segments, entries, exits = _door_segments(room, start, end)
    else:
        segments, entries, exits = _visit_segments(room, start, end)

    max_people = max((c for _, _, c in segments), default=0)
    peak_start = peak_end = None
    longest = at_peak = 0.0
    run_start = None
    for s, e, c in segments + [(end, end, -1)]:
        if c == max_people and max_people > 0:
            at_peak += (e - s).total_seconds()
            run_start = run_start or s
            run_end = e
        elif run_start:
            if (run_end - run_start).total_seconds() > longest:
                longest, peak_start, peak_end = (run_end - run_start).total_seconds(), run_start, run_end
            run_start = None

    person_seconds = sum((e - s).total_seconds() * c for s, e, c in segments)
    avg_people = person_seconds / window_s if window_s else 0
    samples = len(database.get_sample_times(room.name, start, end))

    row.update(
        coverage_pct=round(min(100, samples * config.SAMPLE_INTERVAL_SECONDS / window_s * 100), 1) if window_s else 0,
        entries=entries,
        exits=exits,
        max_people=max_people,
        min_people=min((c for _, _, c in segments), default=0),
        avg_people=round(avg_people, 2),
        avg_occupancy_pct=round(avg_people / room.capacity * 100, 1),
        peak_occupancy_pct=round(max_people / room.capacity * 100, 1),
        peak_start=utc_to_local_hhmm(peak_start) if peak_start else "",
        peak_end=utc_to_local_hhmm(peak_end) if peak_end else "",
        peak_minutes=round(longest / 60, 1),
        minutes_at_peak=round(at_peak / 60, 1),
        occupied_minutes=round(sum((e - s).total_seconds() for s, e, c in segments if c > 0) / 60, 1),
    )
    return row


def _steps(start, end, count, changes):
    """Segments (from, to, head-count) of a step function starting at `count`; changes = [(t, new_count)]."""
    segments, cursor = [], start
    for t, new_count in changes:
        if t > cursor:
            segments.append((cursor, t, count))
            cursor = t
        count = new_count
    if end > cursor:
        segments.append((cursor, end, count))
    return segments


def _visit_segments(room, start, end):
    visits = database.get_visits(room.id, start, end)
    events = []
    for v in visits:
        s, e = max(v.entered_at, start), min(v.exited_at or v.last_seen_at, end)
        if e > s:
            events += [(s, 1), (e, -1)]
    events.sort(key=lambda ev: (ev[0], ev[1]))  # exits before entries at the same instant
    changes, count = [], 0
    for t, delta in events:
        count += delta
        changes.append((t, count))
    entries = sum(1 for v in visits if v.entered_at >= start)
    exits = sum(1 for v in visits if v.exited_at and v.exited_at <= end)
    return _steps(start, end, 0, changes), entries, exits


def _door_segments(room, start, end):
    """Entrance rooms: head-count comes from line crossings (already clamped at 0 when recorded)."""
    events = database.get_door_events(room.id, start, end)
    changes = [(e.timestamp, e.count_after) for e in events]
    entries = sum(1 for e in events if e.direction > 0)
    exits = sum(1 for e in events if e.direction < 0)
    return _steps(start, end, database.get_door_count_at(room.id, start), changes), entries, exits


def daily_report(room, date_from, date_to, open_time=None, close_time=None):
    rows, day = [], date_from
    while day <= date_to:
        row = day_report(room, day, open_time, close_time)
        if row:
            rows.append(row)
        day += timedelta(days=1)
    return rows


def to_csv(rows):
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow([label for _, label in CSV_COLUMNS])
    for row in rows:
        writer.writerow([row[key] for key, _ in CSV_COLUMNS])
    return buf.getvalue()


def today_local():
    return datetime.now(TZ).date()


def parse_date(value, default):
    return date.fromisoformat(value) if value else default
