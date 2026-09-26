# Azure Integration Plan: Serverless Occupancy Analytics

**Status:** proposal · **Date:** 2026-09-26 · **Based on:** the working proof of concept in this
repository (see [architecture.md](architecture.md))

## 1. Goal

Turn the single-host proof of concept into a multi-site service on Microsoft Azure that:

1. counts people per room (zone cameras) and people entering and leaving (entrance cameras),
2. stores the counts and events centrally,
3. produces statistics and reports (utilisation, peaks, flows, trends, forecasts) for building,
   facilities and timetabling staff,
4. runs mostly on **serverless / consumption-billed** services, with sign-in through
   **Microsoft Entra ID**,
5. uses **Azure AI Vision** where it adds value, without sending continuous video to the cloud.

## 2. The key design decision: what runs at the edge and what runs in the cloud

The proof of concept processes **1–3 frames per second per camera** and relies on **tracking
across frames** (ByteTrack) for in/out counting and minimum-stay rules. That shapes the design:

| Constraint | Consequence |
|---|---|
| Entrance counting needs ≥ 3 frames/s and the same person tracked across frames | Must run close to the camera. Stateless per-request functions can't track people across frames without heavy state handling |
| Cloud vision APIs are billed **per image (transaction)** | 3 fps per camera ≈ **259,200 calls/day/camera**, which is too expensive. One image every 30 s ≈ **2,880 calls/day/camera**, which is affordable |
| CCTV cameras sit on private LANs (RTSP) | The cloud can't pull frames. Something on site has to push them |
| Privacy / GDPR | Sending continuous video of people off site needs a much stronger legal basis than sending counts |
| Bandwidth | Video upload per camera costs far more than a few KB of events per minute |

**Recommendation: a hybrid design.**
- **Edge (on site):** a small containerised agent (today's capture code) reads the cameras,
  detects, tracks and counts, and sends only **events and numbers** to Azure. No video leaves
  the site by default.
- **Cloud (serverless):** ingestion, storage, statistics, reports, alerts, dashboards,
  administration, and **Azure AI Vision** for (a) cloud-only counting of low-traffic rooms from
  still images and (b) checking the edge counts' accuracy against an independent model.

A **cloud-only** variant (edge just uploads a still every 30 s, Azure AI Vision counts it) is
included for rooms where installing edge compute isn't possible. It supports zone counting only,
not entrance counting (see §5.3).

## 3. Target architecture

```
 SITE (per building)                                   AZURE (per region)
 ───────────────────                                   ─────────────────────────────────────────────
 IP cameras (RTSP)                                     Azure IoT Hub ── device identity, config, telemetry
      │                                                     │  (message routing)
      ▼                                                     ├──► Event Hubs endpoint ──► Stream Analytics / Fabric Eventstream
 Edge agent container                                       │                              │
  (Azure IoT Edge runtime, or                               │                              ├─► Azure SQL Database (serverless)   curated tables
   Azure IoT Operations / Arc on                            │                              └─► ADLS Gen2 / Fabric Lakehouse     raw event archive
   Kubernetes for larger sites)                             │
  - YOLO + ByteTrack (current code)                         └──► Azure Functions (Flex Consumption)
  - zone visits, door events,                                     - enrich/validate events, maintain live counts
    30 s room samples                                             - daily report builder (timer)
  - sends JSON events (MQTT/AMQP) ──────────────────────►         - alert rules → Logic Apps / Teams
  - optional: still image every N min ──► Blob Storage            - config API for the admin UI
                                           │
                                           └─ Event Grid ──► Function ──► Azure AI Vision (Image Analysis 4.0, People)
                                                                          → cloud count / accuracy check → SQL
 Users ──► Azure Static Web Apps (dashboard + admin, Entra ID sign-in) ──► Functions API ──► SQL
       ──► Power BI (reports, trends, utilisation; row-level security per building)
 Ops   ──► Azure Monitor / Application Insights · Key Vault · Managed identities · Defender for IoT (optional)
```

### 3.1 Services and their role

| Layer | Service | Why this one |
|---|---|---|
| Edge runtime | **Azure IoT Edge** (single device) or **Azure IoT Operations** (Arc-enabled Kubernetes, larger sites) | Deploys and updates the agent container remotely, and buffers messages while offline |
| Device connection | **Azure IoT Hub** | Per-device identity, desired-properties config (zones, lines, thresholds), telemetry routing |
| Stream processing | **Azure Stream Analytics** or **Microsoft Fabric Real-Time Intelligence (Eventstream + Eventhouse)** | Windowed aggregates (per minute, per hour), late-arriving data, direct output to SQL / Power BI |
| Business logic | **Azure Functions** (Flex Consumption plan) | Serverless: reports, alerts, config API, AI Vision calls |
| Operational store | **Azure SQL Database, serverless tier** | Relational model matches today's schema, auto-pauses when idle, and Power BI can read it directly |
| Archive / analytics | **ADLS Gen2** or **Fabric Lakehouse** | Cheap long-term raw events for history and machine learning |
| Images (optional) | **Blob Storage** + **Event Grid** | Triggers the vision pipeline. Short retention enforced by a timer Function (lifecycle rules run only daily) |
| Vision | **Azure AI Vision, Image Analysis 4.0, People feature** | Managed person detection with bounding boxes and confidence, for cloud-only rooms and accuracy checks |
| Web app | **Azure Static Web Apps** + Functions API | Serverless hosting with built-in Entra ID auth. Replaces Flask, gunicorn and Cloudflare Tunnel |
| BI | **Power BI** (or Fabric) | Self-service statistics, scheduled reports, row-level security per building |
| Alerts | **Logic Apps** / **Power Automate** → Teams, e-mail | "Room over capacity", "camera offline > 10 min", "entrance count drifting" |
| Forecasting (later) | **Azure Machine Learning** or **Fabric Data Science** | Predict occupancy by hour, room and weekday for timetabling |
| Security | **Entra ID**, **Managed identities**, **Key Vault**, **Private Endpoints** | No secrets in code, least privilege, no public database endpoint |
| Monitoring | **Azure Monitor**, **Application Insights**, IoT Hub metrics | Health, device heartbeats, costs |

### 3.2 Services considered and rejected (verify before relying on this)

| Service | Status / reason |
|---|---|
| Azure AI Vision **Spatial Analysis** (person counting and line crossing on the edge) | Was the natural fit, but Microsoft announced its **retirement (30 March 2025)**. Don't design around it |
| Azure Video Analyzer | Retired (December 2022) |
| Azure Percept | Retired (2023) |
| Azure AI **Video Indexer** | Built for recorded media insights, billed per minute of video. Not suited to continuous people counting |
| Sending all frames to Image Analysis | Cost (§6) and privacy. It also can't do tracking, so entrance counting and minimum stay aren't possible |

## 4. Data contract (edge → cloud)

Small JSON messages sent to IoT Hub. All times are UTC ISO-8601. IDs match the cloud `room` and
`camera` tables.

```json
// door crossing (entrance camera): sent immediately
{ "type": "door_event", "site": "LON-01", "room_id": "SIN", "camera_id": "cam6",
  "ts": "2026-09-26T19:51:21.457Z", "direction": 1, "edge_count_after": 3, "confidence": 0.71 }

// visit closed (zone camera)
{ "type": "visit", "site": "LON-01", "room_id": "A101", "camera_id": "cam1",
  "entered_at": "2026-09-26T09:02:10Z", "exited_at": "2026-09-26T10:48:55Z" }

// room sample: every 30 s
{ "type": "room_sample", "site": "LON-01", "room_id": "A101", "ts": "2026-09-26T10:00:30Z",
  "people": 17, "capacity": 20, "cameras_online": 2, "cameras_total": 2 }

// camera health: every 60 s
{ "type": "camera_health", "camera_id": "cam6", "ts": "…", "fps": 2.9, "last_error": null }
```

Configuration flows the other way through **IoT Hub desired properties**: the per-camera mode,
zone polygon, entrance line, thresholds and model settings that the admin page stores in SQLite
today.

## 5. Where Azure AI Vision fits

### 5.1 Accuracy check (recommended from the start)
Every 5–15 minutes the edge uploads one still per camera to Blob Storage (faces pixelated if
required, short retention). A Function calls **Image Analysis 4.0 (People)**, applies the
camera's zone polygon to each person's feet point, and stores `cloud_count` next to the edge
count for that moment. Power BI then shows **count agreement per camera**, an ongoing quality
measure that also flags camera moves, obstructions and lighting problems.

### 5.2 Cloud-only counting for simple rooms
For rooms without edge compute, a lightweight uploader (or the camera's own FTP/HTTP snapshot
feature) sends one still every 30–60 s. The pipeline is Blob → Event Grid → Function → Image
Analysis → count in zone → `room_sample`. There's no tracking, so there's no minimum stay and no
in/out flow, only "people present now". This is good enough for utilisation statistics.

### 5.3 Why entrance counting stays on the edge
Counting crossings needs the same person seen on both sides of the line at 3 or more frames per
second. Doing that in the cloud means uploading every frame (cost, bandwidth, privacy) and
keeping tracking state across stateless calls. The edge does this cheaply with the existing
`LineCounter`.

### 5.4 Things to check with Microsoft before committing
- Image Analysis 4.0 **People** is available only in some Azure regions. Check that a UK/EU
  region supports it, for data residency.
- Check current per-1,000-transaction pricing and throttling (transactions per second) for the
  chosen tier.
- Check the status of **custom model training** in Image Analysis 4.0 and Custom Vision before
  planning a fine-tuned person model. It may be better to fine-tune YOLO on site data and run it
  at the edge.

## 6. Cost model (formulas, prices to fill in from the Azure pricing calculator)

| Item | Driver | Formula (per month) |
|---|---|---|
| AI Vision (accuracy check) | cameras × images/day | `cams × (1440 / interval_min) × 30 / 1000 × price_per_1k` |
| AI Vision (cloud-only rooms, 30 s) | 2,880 images/day/camera | `cams × 86.4k / 1000 × price_per_1k` |
| AI Vision (all frames, rejected) | 259,200 images/day/camera at 3 fps | about 90× the 30 s option |
| IoT Hub | messages/day | about 3,000–4,000 messages/day/camera (samples + health + events) → the smallest tier covers many cameras |
| Functions (Flex Consumption) | executions + GB-s | Small: reports and alerts are periodic |
| Azure SQL serverless | vCore-seconds + storage | Auto-pause outside working hours |
| Stream Analytics / Fabric | streaming units / capacity | The main fixed cost. Start with one Stream Analytics job, or a small Fabric capacity if Fabric is already licensed |
| Static Web Apps | plan | Standard plan for custom auth and SLA |
| Power BI | licences | Pro per viewer, or Fabric/Premium capacity for wide distribution |
| Edge hardware | one-off | Mini-PC, 8 cores, about 4 cameras at today's settings. A small GPU (for example NVIDIA Jetson or a GPU in the PC) supports 10+ cameras per site |

Example sizing input from the proof of concept: yolov8s at 1280 px costs about 0.6 s of CPU per
frame (2 threads). Four zone cameras at 1 fps plus one entrance camera at 3 fps use about half
of an 8-core host.

## 7. Statistics the platform produces

| Audience | Statistic | Source |
|---|---|---|
| Facilities | Live occupancy per room/floor/building. Over-capacity alerts | `room_sample`, live counts |
| Facilities | Utilisation % = average occupied seats ÷ capacity, per room, per hour and weekday | samples, visits |
| Facilities | Occupancy rate = % of open hours with ≥ 1 person | visits / samples |
| Timetabling | Booked vs actually used (join with the timetabling / room booking system) | samples + booking data |
| Timetabling | Right-sizing: rooms regularly < 30 % full, or > 90 % full | utilisation |
| Estates | Peak times, peak duration, busiest entrances | reports, door events |
| Estates | In/out flows per entrance and hour, arrival and departure curves | door events |
| Security / H&S | Evacuation count (people still inside per building) | door counts |
| Management | Trends week-on-week and term-on-term, forecasts per room and hour | Lakehouse + ML |
| Operations | Camera uptime, data coverage %, edge vs cloud count agreement | health, accuracy check |

All reports keep today's rules: local time (daylight saving aware), report windows per room,
and coverage shown next to every figure.

## 8. Security and privacy

- **Data minimisation:** by default only counts and events leave the site. Stills are used only
  for the accuracy check and cloud-only rooms, with pixelated faces, retention of hours not days,
  and private containers reached through managed identity.
- Complete a **DPIA** before rollout. Put up signage, write a retention policy, and document the
  lawful basis. Keep "counts only, no identification": no face recognition, no re-identification
  across cameras.
- **Identity:** Entra ID for users (roles: Viewer, Facilities, Admin). Managed identities for
  Functions to SQL, Blob and Vision. Device identities through IoT Hub (X.509 certificates in
  production).
- **Network:** Private Endpoints for SQL, Storage and Vision. The edge only needs outbound HTTPS
  / AMQP / MQTT to IoT Hub, with no inbound ports (this replaces Cloudflare Tunnel).
- **Secrets:** Key Vault (camera credentials pushed to the edge as encrypted module settings,
  never stored in the SQL config tables).

## 9. Delivery plan

| Phase | Scope | Outcome | Indicative effort |
|---|---|---|---|
| **0. Harden the PoC** (current) | Real RTSP cameras instead of YouTube. Validate accuracy against manual counts for 1–2 weeks. Fix the YouTube reader only if still needed | Known accuracy per room type | 1–2 weeks |
| **1. Edge → cloud telemetry** | Package the capture code as a container. Add an IoT Hub publisher (§4) with offline buffering. Provision IoT Hub, SQL serverless and one Function for ingestion | Same numbers as today, stored centrally. The existing dashboard keeps running | 2–3 weeks |
| **2. Serverless analytics and UI** | Stream Analytics (or Fabric) aggregates. Static Web Apps dashboard and admin with Entra ID. Config pushed through IoT Hub desired properties. Power BI utilisation and flow reports | Flask, gunicorn and Cloudflare retired. Multi-site | 4–6 weeks |
| **3. Azure AI Vision** | Accuracy-check pipeline (§5.1). Cloud-only rooms (§5.2) where edge isn't possible | Measured accuracy and a cheaper option for simple rooms | 2–3 weeks |
| **4. Scale and insight** | Fleet deployment through IoT Edge / IoT Operations. Timetable/booking integration. Forecasting. Alerts in Teams | Estate-wide utilisation and planning data | ongoing |

Each phase can be stopped after delivery with a working system.

## 10. Risks and open questions

| Risk / question | Mitigation |
|---|---|
| Entrance counts drift (missed crossings) | Nightly reset at closing time. Compare with a zone camera where one exists. Alert when the count stays > 0 after hours |
| Detection accuracy differs by room layout | Accuracy check (§5.1). Choose model and resolution per camera. Fine-tune YOLO on site data if needed |
| Edge device failure | IoT Edge offline buffering, health alerts, spare device image |
| Azure AI Vision region, pricing or feature changes | Keep the edge model as the primary counter. Vision is an add-on, not a dependency |
| Privacy approval | DPIA early (phase 0). Counts-only design |
| **Open:** which sites, rooms and cameras (count, resolution, RTSP availability)? | Needed for hardware sizing and cost |
| **Open:** Fabric already licensed? | Decides Stream Analytics + SQL versus Fabric Real-Time Intelligence |
| **Open:** room booking / timetable system and API? | Needed for the "booked vs used" statistic |
| **Open:** required data residency (UK South / UK West)? | Decides region and Vision availability |
