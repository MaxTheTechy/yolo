import database


def compute_occupancy_percent(people_count, room_capacity):
    return round((people_count / room_capacity) * 100, 2)


def record(room, people_count, snapshot_path=None):
    occupancy_percent = compute_occupancy_percent(people_count, room.capacity)
    database.record_occupancy(
        room_name=room.name,
        people_count=people_count,
        occupancy_percent=occupancy_percent,
        snapshot_path=snapshot_path,
    )
    return occupancy_percent
