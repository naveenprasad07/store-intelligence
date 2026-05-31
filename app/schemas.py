from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from enum import Enum


class EventType(str, Enum):
    ENTRY = "entry"
    EXIT = "exit"
    ZONE_ENTER = "zone_enter"
    ZONE_EXIT = "zone_exit"
    DWELL = "dwell"
    TRIAL = "trial"
    BILLING = "billing"


class PersonEvent(BaseModel):
    event_id: str
    event_type: EventType
    track_id: int
    camera_id: str
    zone_id: Optional[str] = None
    timestamp: datetime
    confidence: float = Field(ge=0.0, le=1.0)
    is_staff: bool = False
    bbox: Optional[List[float]] = None  # [x1, y1, x2, y2]
    session_id: Optional[str] = None


class TrafficMetrics(BaseModel):
    date: str
    hour: Optional[int] = None
    total_entries: int
    total_exits: int
    unique_visitors: int
    current_occupancy: int
    avg_dwell_minutes: float
    peak_hour: Optional[int] = None
    staff_count: int


class FunnelMetrics(BaseModel):
    date: str
    store_entries: int
    zone_engagements: int
    trial_area_visits: int
    billing_reaches: int
    conversion_rate: float  # billing / entries
    zone_engagement_rate: float
    trial_rate: float


class ZoneDwellMetrics(BaseModel):
    zone_id: str
    zone_name: str
    avg_dwell_seconds: float
    total_visits: int
    unique_visitors: int
    hotspot_score: float  # 0-1


class AnomalyEvent(BaseModel):
    anomaly_id: str
    anomaly_type: str
    description: str
    severity: str  # low / medium / high
    detected_at: datetime
    camera_id: str
    track_ids: List[int] = []


class MetricsResponse(BaseModel):
    store_id: str
    generated_at: datetime
    traffic: TrafficMetrics
    funnel: FunnelMetrics
    zone_dwells: List[ZoneDwellMetrics]
    anomalies: List[AnomalyEvent]
    pipeline_status: str
