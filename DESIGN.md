# DESIGN.md — Store Intelligence System
## Purplle Tech Challenge 2026 · Brigade Road, Bangalore

---

## 1. System Overview

```
┌──────────────┐     ┌─────────────────────┐     ┌────────────┐     ┌──────────┐
│  CCTV .mp4   │────▶│  Detection Pipeline  │────▶│    Redis   │────▶│  FastAPI │
│  (5 cameras) │     │  YOLOv8 + DeepSORT   │     │  (events)  │     │   /api   │
└──────────────┘     └─────────────────────┘     └────────────┘     └──────────┘
                              │                                            │
                              ▼                                            ▼
                     ┌──────────────┐                            ┌──────────────┐
                     │  Event JSON  │                            │   Dashboard  │
                     │  (per track) │                            │   /metrics   │
                     └──────────────┘                            └──────────────┘
```

The system has two decoupled services:

1. **Detector** — reads video files, runs CV pipeline, POSTs structured events to the API
2. **API** — receives events, aggregates metrics, serves all endpoints

They communicate via Redis (event log) and HTTP (ingest endpoints). This means:
- The API works independently even if the detector is still running
- Multiple detectors can process different camera feeds in parallel
- Events are persisted in Redis and survive restarts

---

## 2. Detection Pipeline

### Person Detection
- **Model**: YOLOv8n (nano) — chosen for its balance of speed and accuracy on CPU/GPU
- **Class filter**: class=0 (person only), confidence threshold 0.45
- Runs on every frame; can be down-sampled to every 2nd frame for low-power hardware

### Person Tracking
- **Tracker**: DeepSORT with appearance re-ID embeddings
- Handles occlusion (person behind shelf) and short disappearances (max_age=30 frames)
- Each person gets a unique `track_id` that persists across their visit

### Entry / Exit Detection
- Two virtual horizontal lines are defined as fractions of frame height (`entry_line_y`, `exit_line_y`) loaded from `store_layout.json`
- A track crossing `entry_line` downward → `ENTRY` event
- A track crossing `exit_line` downward → `EXIT` event
- 10-second cooldown prevents double-counting from jitter

### Re-entry Handling
- DeepSORT's re-ID embeddings will re-assign the same `track_id` if the person re-enters within `max_age` frames
- For longer re-entries, a new `track_id` is assigned but the same `customer_number` concept still holds at session level via `session_id`

### Staff Detection
- Any tracked person who spends > 30 minutes continuously near the billing zone is flagged `is_staff = True`
- All staff events are still stored but excluded from visitor metrics

### Zone Mapping
- Frame is divided into a spatial grid aligned with the known store layout
- Each bounding box centroid is mapped to the nearest zone
- Zone transitions generate `ZONE_ENTER` / `ZONE_EXIT` events with dwell time

---

## 3. Event Schema

```json
{
  "event_id":   "uuid",
  "event_type": "entry | exit | zone_enter | zone_exit | dwell | trial | billing",
  "track_id":   42,
  "camera_id":  "cam_01",
  "zone_id":    "makeup",
  "timestamp":  "2026-04-10T14:23:00Z",
  "confidence": 0.87,
  "is_staff":   false,
  "bbox":       [120, 80, 200, 340],
  "session_id": "uuid"
}
```

All events are appended to a Redis list keyed by date (`events:2026-04-10`), allowing efficient time-range queries.

---

## 4. Business Metrics

### Traffic
- `total_entries` / `total_exits` / `unique_visitors` — derived from ENTRY/EXIT events, deduped by track_id
- `current_occupancy` = entries − exits (floored at 0)
- `avg_dwell_minutes` = mean(exit_time − entry_time) across all sessions with both entry and exit events

### Conversion Funnel
```
Store Entries → Zone Engagement → Trial Area → Billing Counter
```
Each step is deduplicated by track_id (one person counts once per stage regardless of how many times they visit).

### Anomaly Detection
| Anomaly | Trigger |
|---|---|
| Group entry | ≥3 persons crossing entry line within 2 seconds |
| Loitering | Single person in entry/exit zone for >10 minutes |
| Overcrowding | Occupancy > configured max_capacity |
| Pipeline error | Detector crash or camera disconnect |

---

## 5. API Endpoints

| Method | Path | Description |
|---|---|---|
| GET | `/metrics` | All metrics (traffic + funnel + zones + anomalies) |
| GET | `/traffic` | Entry/exit counts, occupancy, dwell |
| GET | `/funnel` | Conversion funnel |
| GET | `/zones` | Zone dwell heatmap |
| GET | `/occupancy` | Live headcount |
| GET | `/anomalies` | Detected anomalies |
| GET | `/events` | Raw event log (debug) |
| POST | `/ingest/event` | Detector → API event push |
| GET | `/health` | Health check |
| GET | `/ready` | Readiness check |

---

## 6. Production Readiness

- **Docker Compose**: single `docker compose up` spins up Redis + API + Detector
- **Health checks**: Redis health gate before API/Detector start
- **Structured logging**: JSON-formatted with log levels
- **Graceful shutdown**: video capture released on SIGTERM
- **Retry logic**: detector retries event POSTs on transient failures
- **Data retention**: Redis TTL of 7 days on all event lists
