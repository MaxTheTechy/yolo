# Classroom Occupancy Analytics - Proof of Concept

## Objective

Build a proof of concept (PoC) that:

* Uses an existing IP CCTV camera.
* Reads the RTSP stream.
* Detects people using YOLO.
* Counts the number of people visible.
* Stores occupancy data in Microsoft SQL Server.
* Displays analytics through a web dashboard.
* Provides a foundation for future classroom occupancy monitoring.

---

# Phase 1 - Initial PoC

## Success Criteria

The system should:

* Connect to an IP camera.
* Detect people in real time.
* Display the current count.
* Store historical occupancy data.

No booking system integration is required during this phase.

---

# Architecture

```text
IP Camera
    ↓ RTSP
Python Application
    ↓
YOLO Detection
    ↓
Occupancy Engine
    ↓
SQL Server (can be sqlite)
    ↓
Dashboard
```

---

# Recommended Hardware

## Option 1 (Preferred)

* Ubuntu Server 2022
* 8 GB RAM minimum

## Option 2

* Existing PC

## Option 3

* Raspberry Pi 5
* Suitable only for light testing.

---

# Software Stack

## Python

Version:

```text
Python 3.11+
```

## Required Packages

```bash
pip install ultralytics
pip install opencv-python
pip install flask
pip install sqlalchemy
pip install pyodbc
pip install supervision
pip install pandas
```

---

# Project Structure

```text
occupancy-poc/
│
├── app.py
├── requirements.txt
├── config.py
├── database.py
├── detector.py
├── tracker.py
├── occupancy.py
├── dashboard/
│   ├── app.py
│   ├── templates/
│   └── static/
│
├── models/
│   └── yolov8n.pt
│
├── logs/
│
└── README.md
```

---

# Configuration

## config.py

```python
RTSP_URL = "rtsp://username:password@camera-ip/stream"

SQL_SERVER = "server"
SQL_DATABASE = "Occupancy"

SAMPLE_INTERVAL_SECONDS = 30
ROOM_CAPACITY = 8
ROOM_NAME = "A101"
```

---

# Verify Camera Stream

Install VLC.

Open:

```text
rtsp://username:password@camera-ip/stream
```

If VLC can display the stream, Python can read it.

---

# Step 1 - Connect to Camera

```python
import cv2

cap = cv2.VideoCapture(RTSP_URL)
```

---

# Step 2 - Load YOLO

```python
from ultralytics import YOLO

model = YOLO("yolov8n.pt")
```

---

# Step 3 - Detect People

Class ID:

```text
0 = Person
```

Pseudo-code:

```python
results = model(frame)

people_count = 0

for r in results:
    for box in r.boxes:
        cls = int(box.cls[0])

        if cls == 0:
            people_count += 1
```

---

# Occupancy Formula

```text
occupancy_percentage =
(current_people / room_capacity) * 100
```

Example:

```text
Room capacity: 8
People detected: 5
Occupancy: 62.5%
```

---

# SQL Server Database

## Create Database

```sql
CREATE DATABASE Occupancy;
GO
```

---

## Create Table

```sql
CREATE TABLE RoomOccupancy
(
    Id BIGINT IDENTITY(1,1) PRIMARY KEY,
    RoomName VARCHAR(50),
    Timestamp DATETIME2,
    PeopleCount INT,
    OccupancyPercent DECIMAL(5,2)
);
```

---

# Example Record

```text
Room: A101
Timestamp: 2026-06-30 10:15:00
PeopleCount: 5
OccupancyPercent: 62.5
```

---

# Dashboard Requirements

## Current View

* Current occupancy
* Current occupancy percentage

## Historical View

* Hourly occupancy
* Daily occupancy
* Peak occupancy

---

# Future Booking Integration

Possible metrics:

```text
Booked Capacity
Actual Occupancy
Utilisation %
```

Example:

| Time  | Booked | Actual | Utilisation |
| ----- | ------ | ------ | ----------- |
| 09:00 | 8      | 3      | 37.5%       |
| 11:00 | 8      | 8      | 100%        |
| 14:00 | 8      | 1      | 12.5%       |

---

# Multi-Camera Support (Future)

## Option 1

Two independent zones.

## Option 2

Multi-camera fusion.

## Option 3 (Recommended for Classrooms)

Seat occupancy detection.

---

# Seat Occupancy Detection

For classrooms with fixed desks:

```text
Seat 1
Seat 2
Seat 3
Seat 4
Seat 5
Seat 6
Seat 7
Seat 8
```

The system determines:

```text
Seat 1 occupied
Seat 2 empty
Seat 3 occupied
...
```

Advantages:

* Extremely high accuracy.
* No need to identify individuals.
* No double counting.
* Excellent GDPR compliance.

---

# GDPR Considerations

Recommended approach:

* Process video locally.
* Do not store video.
* Do not store images.
* Store only occupancy metrics.

Store only:

```text
timestamp
room_name
people_count
occupancy_percent
```

---

# Future Enhancements

* Booking system integration.
* Room utilisation reports.
* Automatic room release.
* Occupancy heat maps.
* Energy management integration.
* Alerts for overcrowding.
* Multiple classroom support.

---

# Phase 2 Goals

* Tracking with ByteTrack.
* Dashboard improvements.
* Multi-camera support.
* Seat occupancy detection.
* API endpoints.
* Docker deployment.
* Authentication.
* Reporting engine.

---

# Phase 3 Goals

* Production deployment.
* Multiple buildings.
* Central analytics server.
* Historical trends.
* Predictive room utilisation.
* University-wide reporting.
