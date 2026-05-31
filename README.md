# Store Intelligence System
### Purplle Tech Challenge 2026 — Round 2

An end-to-end pipeline that ingests raw CCTV footage and produces real-time store intelligence metrics.

---

## Quick Start

### Prerequisites
- Docker & Docker Compose
- Place your `.mp4` video files in the `videos/` directory

```bash
mkdir videos
cp /path/to/your/*.mp4 videos/
```

### Run

```bash
docker compose up
```

The API will be available at **http://localhost:8000**

The detector starts automatically and processes all `.mp4` files in `videos/`.

---

## API Reference

| Endpoint | Description |
|---|---|
| `GET /metrics` | All store intelligence metrics |
| `GET /traffic` | Entry/exit counts, occupancy, dwell time |
| `GET /funnel` | Conversion funnel (entries → billing) |
| `GET /zones` | Zone dwell heatmap |
| `GET /occupancy` | Live headcount |
| `GET /anomalies` | Detected anomalies |
| `GET /health` | Health check |
| `GET /docs` | Interactive API docs (Swagger UI) |

### Example

```bash
curl http://localhost:8000/metrics | python -m json.tool
curl http://localhost:8000/funnel
curl http://localhost:8000/traffic?hour=14
```

---

## Architecture

See [DESIGN.md](DESIGN.md) for full system architecture.

See [CHOICES.md](CHOICES.md) for engineering trade-off decisions.

```
videos/*.mp4
    └──▶ YOLOv8n detection
         └──▶ DeepSORT tracking
              └──▶ Event generation (entry/exit/zone/anomaly)
                   └──▶ Redis (event log)
                        └──▶ FastAPI /metrics, /funnel, /traffic ...
```

---

## Running Tests

```bash
pip install pytest pytest-asyncio
pytest tests/ -v
```

---

## Configuration

Edit `store_layout.json` to adjust:
- Zone definitions and camera coverage
- Entry/exit line positions (`entry_line_y_fraction`, `exit_line_y_fraction`)
- Operating hours

Environment variables:
| Variable | Default | Description |
|---|---|---|
| `REDIS_URL` | `redis://localhost:6379` | Redis connection |
| `VIDEO_DIR` | `./videos` | Directory with .mp4 files |
| `LOG_LEVEL` | `INFO` | Logging verbosity |
| `STORE_ID` | `Brigade_Bangalore` | Store identifier |

---

## Store Data

The system is pre-configured for **Purplle Brigade Road, Bangalore** with:
- 5 camera zones (entry, floor-left, floor-right, floor-center, billing)
- 9 store zones (skin, makeup, hair, bath & body, fragrance, personal care, trial area, billing)
- Operating hours: 10:00–22:00
