import logging
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Optional, List
import os

from fastapi import FastAPI, Query, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.schemas import MetricsResponse, FunnelMetrics, PersonEvent, AnomalyEvent
from app.state import StateManager
from app.metrics import build_metrics_response

logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
log = logging.getLogger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
STORE_ID  = os.getenv("STORE_ID", "Brigade_Bangalore")

state = StateManager(REDIS_URL)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await state.connect()
    await state.set_pipeline_status("idle")
    log.info("API started, Redis connected")
    yield
    await state.close()


app = FastAPI(
    title="Store Intelligence API",
    description="Real-time retail analytics from CCTV footage — Purplle Tech Challenge 2026",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Health / Readiness ─────────────────────────────────────────────────────

@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok", "timestamp": datetime.utcnow().isoformat()}


@app.get("/ready", tags=["system"])
async def ready():
    try:
        status = await state.get_pipeline_status()
        return {"ready": True, "pipeline_status": status}
    except Exception as e:
        raise HTTPException(status_code=503, detail=str(e))


# ─── Core Metrics endpoint (mandatory per spec) ──────────────────────────────

@app.get("/metrics", response_model=MetricsResponse, tags=["analytics"])
async def get_metrics(for_date: Optional[date] = Query(default=None)):
    """
    Returns all store intelligence metrics for a given date (defaults to today).
    Includes: traffic counts, funnel, zone dwell, anomalies, pipeline status.
    """
    response = await build_metrics_response(state, STORE_ID)
    return response


# ─── Traffic ────────────────────────────────────────────────────────────────

@app.get("/traffic", tags=["analytics"])
async def get_traffic(hour: Optional[int] = Query(default=None, ge=0, le=23)):
    """Entry/exit counts, occupancy, dwell time. Optionally filtered by hour."""
    from app.metrics import _compute_traffic
    from app.schemas import EventType

    events = await state.get_events()
    if hour is not None:
        events = [e for e in events if e.timestamp.hour == hour]
    traffic = _compute_traffic(events)
    if hour is not None:
        traffic.hour = hour
    return traffic


# ─── Conversion Funnel ───────────────────────────────────────────────────────

@app.get("/funnel", response_model=FunnelMetrics, tags=["analytics"])
async def get_funnel():
    """
    Store conversion funnel:
      entries → zone engagements → trial area → billing
    Each stage is session-deduped (no double counting).
    """
    from app.metrics import _compute_traffic, _compute_funnel
    events = await state.get_events()
    traffic = _compute_traffic(events)
    return _compute_funnel(events, traffic)


# ─── Zone Heatmap ────────────────────────────────────────────────────────────

@app.get("/zones", tags=["analytics"])
async def get_zone_metrics():
    """Dwell time and visit counts per zone."""
    from app.metrics import _compute_zone_dwells
    events = await state.get_events()
    return _compute_zone_dwells(events)


# ─── Live Occupancy ──────────────────────────────────────────────────────────

@app.get("/occupancy", tags=["analytics"])
async def get_occupancy():
    count = await state.get_occupancy()
    return {"current_occupancy": count, "timestamp": datetime.utcnow().isoformat()}


# ─── Anomalies ───────────────────────────────────────────────────────────────

@app.get("/anomalies", response_model=List[AnomalyEvent], tags=["analytics"])
async def get_anomalies():
    return await state.get_anomalies()


# ─── Events stream (debug / audit) ──────────────────────────────────────────

@app.get("/events", response_model=List[PersonEvent], tags=["debug"])
async def get_events(limit: int = Query(default=100, le=5000)):
    events = await state.get_events()
    return events[-limit:]


# ─── Ingest endpoint (called by detector) ────────────────────────────────────

@app.post("/ingest/event", tags=["ingest"])
async def ingest_event(event: PersonEvent):
    await state.push_event(event)

    # Update live occupancy
    from app.schemas import EventType
    if event.event_type == EventType.ENTRY and not event.is_staff:
        occ = await state.get_occupancy()
        await state.set_occupancy(occ + 1)
    elif event.event_type == EventType.EXIT and not event.is_staff:
        occ = await state.get_occupancy()
        await state.set_occupancy(max(0, occ - 1))

    return {"accepted": True, "event_id": event.event_id}


@app.post("/ingest/anomaly", tags=["ingest"])
async def ingest_anomaly(anomaly: AnomalyEvent):
    await state.push_anomaly(anomaly)
    return {"accepted": True, "anomaly_id": anomaly.anomaly_id}
