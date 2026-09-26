# Azure Integration Plan: Serverless Occupancy Analytics

**Status:** proposal, revision 2 (Microsoft Foundry review) · **Date:** 2026-09-26 · **Based on:**
the working proof of concept in this repository (see [architecture.md](architecture.md))

> **Revision 2 change:** Azure Vision **Image Analysis**, including the People detection revision 1
> relied on, retires on **25 September 2028**, and so does **Custom Vision**. Microsoft asks
> customers to have a migration plan by 25 September 2026. This revision therefore:
> - moves cloud vision to **our own detector as a serverless endpoint**,
> - uses **Microsoft Foundry** for what it's good at: an agent that answers questions about the
>   occupancy data, generated reports, and a generative "second opinion" on camera images,
> - uses **Azure Machine Learning** (Foundry hubs) for training and versioning the detector.

## 1. Goal

Turn the single-host proof of concept into a multi-site service on Microsoft Azure that:

1. counts people per room (zone cameras) and people entering and leaving (entrance cameras),
2. stores the counts and events centrally,
3. produces statistics and reports (utilisation, peaks, flows, trends, forecasts) for building,
   facilities and timetabling staff,
4. runs mostly on **serverless / consumption-billed** services, with sign-in through
   **Microsoft Entra ID**,
5. uses **Microsoft Foundry** (AI models, agents, Azure Machine Learning) where it adds value,
   without sending continuous video to the cloud or depending on vision APIs that are being retired.

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
  administration, a **cloud detector endpoint** for (a) cloud-only counting of low-traffic rooms
  from still images and (b) checking the edge counts' accuracy, and **Microsoft Foundry** for
  questions in plain English, narrative reports and model lifecycle.

A **cloud-only** variant (edge just uploads a still every 30 s, the cloud detector counts it) is
included for rooms where installing edge compute isn't possible. It supports zone counting only,
not entrance counting (see §5.5).

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
                                           └─ Event Grid ──► Container Apps (serverless, scale-to-zero)
                                                             our detector (YOLO/RT-DETR, from AML registry)
                                                             → cloud count / accuracy check → SQL
 Microsoft Foundry ── Agent Service (occupancy agent: SQL/Fabric tools, Teams) · GPT vision "second opinion"
                   └─ Azure Machine Learning: labelling → training → model registry → edge + cloud deployment
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
| Vision (cloud) | **Our detector on Azure Container Apps** (serverless, scale to zero), or an **Azure ML managed online endpoint** | Same or larger model as the edge, for cloud-only rooms and accuracy checks. Pay per second of compute, with no per-image API fee and no retirement risk |
| Model lifecycle | **Azure Machine Learning** (Foundry hub/project): data labelling, training, model registry | Fine-tune the detector on our own camera footage, version it, deploy the same model to edge and cloud |
| AI insights | **Microsoft Foundry Agent Service** + Foundry models (GPT family) | "Occupancy assistant": plain-English questions over SQL/Fabric, weekly narrative reports, anomaly explanations, available in Teams |
| Second opinion (optional) | **GPT vision models in Foundry** or **Content Understanding** | Occasional checks such as "is the camera blocked or moved?" or "roughly how many people?". Not used for counting |
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
| Azure Vision **Image Analysis 4.0** (People) | **Retires 25 September 2028** (all versions, cloud and containers). Microsoft's migration guide points to GPT models and Content Understanding, not to a like-for-like detector |
| **Custom Vision** | **Retires 25 September 2028**. Use Azure ML (or our own training) instead |
| **Face API** | Not needed and deliberately avoided: identity features are Limited Access and would change the privacy case. We count people, we don't identify them |
| GPT vision / Content Understanding **as the counter** | Generative models are flexible but not precise counters in crowded scenes, and cost more per image than a detector. Use them only for occasional second opinions |
| **Foundry Local** for edge detection | Built for small language and vision-language models on PCs. A dedicated detector (YOLO) is far faster for 1–3 fps counting. It may still be useful later for on-site summaries |
| Azure Video Analyzer | Retired (December 2022) |
| Azure Percept | Retired (2023) |
| Azure AI **Video Indexer** | Built for recorded media insights, billed per minute of video. Not suited to continuous people counting |
| Sending all frames to any cloud vision service | Cost (§6) and privacy. Stateless per-image calls also can't do tracking, so entrance counting and minimum stay aren't possible |

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

## 5. Where Microsoft Foundry fits

Microsoft Foundry is the umbrella for Azure's AI platform: models (Azure OpenAI and others), Agent
Service, "Foundry Tools" (the former Azure AI services such as Vision, Speech and Language), AI
hubs and projects, and Azure Machine Learning. Only some of it is useful for occupancy counting:

| Foundry component (as in the portal) | Use here | Verdict |
|---|---|---|
| **Foundry / AI Hubs** (projects) | Home for the agent, model deployments, evaluations and connections (SQL, Fabric, AI Search) | **Use** |
| **Azure OpenAI** (GPT models) | The agent's reasoning, narrative reports, and occasional image second opinions | **Use** |
| **Machine Learning** | Labelling, training and registry for our person detector | **Use** |
| **AI Search** | Only if the agent must also search documents (policies, timetables as PDFs) | Optional |
| **Computer vision** (Image Analysis) | Retires in 2028 | **Avoid** for new work |
| **Custom vision** | Retires in 2028 | **Avoid** |
| **Face API** | Identification isn't wanted | **Avoid** |
| **Bot services** | Superseded for this purpose by Agent Service publishing to Teams | Not needed |
| Content safety, Document intelligence, Language, Speech, Translator, Immersive reader, Health Insights | No occupancy use case | Not needed |

### 5.1 Occupancy assistant (Foundry Agent Service)
An agent with read-only tools over the curated data (Azure SQL, or a Fabric data agent over the
Lakehouse). Users can ask it things like:
- "Which rooms were under 30 % used on Tuesdays this term?"
- "Why was A101 empty yesterday morning?" It checks camera health, coverage and the booking data.
- "Send the facilities team the weekly utilisation summary." (scheduled, posted in Teams)

Guard-rails:
- read-only database role, aggregated views only (never raw events or images),
- Entra ID sign-in, with row-level security carried through to the agent's queries,
- Foundry evaluations run on a fixed set of test questions before each change.

### 5.2 Accuracy check
Every 5–15 minutes the edge uploads one still per camera to Blob Storage (faces pixelated if
required, short retention). Event Grid triggers a **Container Apps** job running a **larger
detector** than the edge uses, for example YOLO "x" size or RT-DETR at full resolution. It applies
the camera's zone polygon and stores `cloud_count` next to the edge count. Power BI shows **count
agreement per camera**, which also flags camera moves, obstructions and lighting problems.
Optionally, a GPT vision model gets the same still with the question "Is the view obstructed or
changed?", which is good at spotting problems even though it isn't precise at counting.

### 5.3 Cloud-only counting for simple rooms
For rooms without edge compute, a lightweight uploader (or the camera's own FTP/HTTP snapshot
feature) sends one still every 30–60 s. The pipeline is Blob → Event Grid → Container Apps detector
→ count in zone → `room_sample`. There's no tracking, so there's no minimum stay and no in/out
flow, only "people present now". This is good enough for utilisation statistics.

### 5.4 Model lifecycle (Azure Machine Learning)
1. Collect short clips per camera type (blurred where required) and label people, heads or
   top-down views with Azure ML data labelling.
2. Fine-tune the detector (YOLO / RT-DETR) on Azure ML compute that is **billed only while
   training** (low-priority VMs).
3. Evaluate against the ground-truth set (count error, entrance drift) and register the model
   version.
4. Deploy the same version to the edge (IoT Edge module) and to the cloud endpoint, keeping the
   version number in every event for traceability.

### 5.5 Why entrance counting stays on the edge
Counting crossings needs the same person seen on both sides of the line at 3 or more frames per
second. Doing that in the cloud means uploading every frame (cost, bandwidth, privacy) and
keeping tracking state across stateless calls. The edge does this cheaply with the existing
journey-based `LineCounter`.

### 5.6 Things to check with Microsoft before committing
- Region availability (UK South / UK West) for Agent Service, the chosen GPT model and
  Container Apps workload profiles, for data residency.
- GPT model pricing per 1M tokens, and the image-token cost for second-opinion checks.
- Agent Service support for the chosen data connection (Azure SQL tool or Fabric data agent) at
  the time of build. These features change quickly.

## 6. Cost model (formulas, prices to fill in from the Azure pricing calculator)

| Item | Driver | Formula (per month) |
|---|---|---|
| Cloud detector (accuracy check) | cameras × images/day × seconds per image | `cams × (1440 / interval_min) × 30 × sec_per_image × price_per_vCPU_s`. About 1 s of CPU per image for a large model, and scale to zero between images |
| Cloud detector (cloud-only rooms, 30 s) | 2,880 images/day/camera | Same formula with 86,400 images/month/camera. Consider one always-on small container once it exceeds about 20 cameras |
| Cloud vision on every frame (rejected) | 259,200 images/day/camera at 3 fps | About 90× the 30 s option |
| Foundry agent (GPT) | questions × tokens | Small for internal use: a typical question with a SQL tool call is a few thousand tokens |
| GPT vision second opinion | images × image tokens | Keep it hourly or on demand. It's much more expensive per image than a detector |
| Azure ML training | GPU hours per training run | Occasional. Low-priority compute, no idle cost |
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
| **3. Foundry and cloud vision** | Container Apps detector for the accuracy check (§5.2) and cloud-only rooms (§5.3). Azure ML labelling, fine-tuning and registry (§5.4). Occupancy assistant in Teams (§5.1) | Measured accuracy, a better model, plain-English insights | 4–6 weeks |
| **4. Scale and insight** | Fleet deployment through IoT Edge / IoT Operations. Timetable/booking integration. Forecasting. Alerts in Teams | Estate-wide utilisation and planning data | ongoing |

Each phase can be stopped after delivery with a working system.

## 10. Risks and open questions

| Risk / question | Mitigation |
|---|---|
| Entrance counts drift (missed crossings) | Nightly reset at closing time. Compare with a zone camera where one exists. Alert when the count stays > 0 after hours |
| Detection accuracy differs by room layout | Accuracy check (§5.2). Choose model and resolution per camera. Fine-tune on site data with Azure ML (§5.4) |
| Edge device failure | IoT Edge offline buffering, health alerts, spare device image |
| Azure AI service retirements (Image Analysis and Custom Vision in 2028, Spatial Analysis in 2025) | Counting depends only on our own model (edge + Container Apps). Foundry is used for insights, where changes are low-risk |
| Agent gives wrong answers | Read-only aggregated views, Foundry evaluations with fixed test questions, show the SQL used with each answer |
| Privacy approval | DPIA early (phase 0). Counts-only design |
| **Open:** which sites, rooms and cameras (count, resolution, RTSP availability)? | Needed for hardware sizing and cost |
| **Open:** Fabric already licensed? | Decides Stream Analytics + SQL versus Fabric Real-Time Intelligence |
| **Open:** room booking / timetable system and API? | Needed for the "booked vs used" statistic |
| **Open:** required data residency (UK South / UK West)? | Decides region and Foundry model/agent availability |
| **Open:** Microsoft 365 / Teams in use? | Decides whether the occupancy assistant is published to Teams |
