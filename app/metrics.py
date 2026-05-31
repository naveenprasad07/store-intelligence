from datetime import date, datetime
from typing import List, Optional
from collections import defaultdict

from app.schemas import (
    PersonEvent, EventType, TrafficMetrics, FunnelMetrics,
    ZoneDwellMetrics, AnomalyEvent, MetricsResponse
)
from app.state import StateManager


ZONE_NAMES = {
    "entry": "Entry / Billing",
    "skin": "Skin Care",
    "makeup": "Makeup",
    "hair": "Hair Care",
    "bath_body": "Bath & Body",
    "fragrance": "Fragrance",
    "personal_care": "Personal Care",
    "trial_area": "Trial / Tester Area",
    "billing": "Billing Counter",
}

PRODUCT_ZONES = {"skin", "makeup", "hair", "bath_body", "fragrance", "personal_care"}


def _compute_traffic(events: List[PersonEvent]) -> TrafficMetrics:
    entries = [e for e in events if e.event_type == EventType.ENTRY and not e.is_staff]
    exits   = [e for e in events if e.event_type == EventType.EXIT  and not e.is_staff]
    staff   = {e.track_id for e in events if e.is_staff}

    unique_visitors = len({e.track_id for e in entries})

    entry_times = {e.track_id: e.timestamp for e in entries}
    exit_times  = {e.track_id: e.timestamp for e in exits}
    dwells = []
    for tid, et in entry_times.items():
        if tid in exit_times:
            dwells.append((exit_times[tid] - et).total_seconds() / 60)
    avg_dwell = round(sum(dwells) / len(dwells), 2) if dwells else 0.0

    hour_counts: dict = defaultdict(int)
    for e in entries:
        hour_counts[e.timestamp.hour] += 1
    peak_hour = max(hour_counts, key=hour_counts.get) if hour_counts else None

    return TrafficMetrics(
        date=date.today().isoformat(),
        total_entries=len(entries),
        total_exits=len(exits),
        unique_visitors=unique_visitors,
        current_occupancy=max(0, len(entries) - len(exits)),
        avg_dwell_minutes=avg_dwell,
        peak_hour=peak_hour,
        staff_count=len(staff),
    )


def _compute_funnel(events: List[PersonEvent], traffic: TrafficMetrics) -> FunnelMetrics:
    non_staff = [e for e in events if not e.is_staff]
    entries = traffic.total_entries or 1

    zone_visitors    = {e.track_id for e in non_staff if e.event_type == EventType.ZONE_ENTER and e.zone_id in PRODUCT_ZONES}
    trial_visitors   = {e.track_id for e in non_staff if e.event_type == EventType.ZONE_ENTER and e.zone_id == "trial_area"}
    billing_visitors = {e.track_id for e in non_staff if e.event_type == EventType.ZONE_ENTER and e.zone_id == "billing"}

    return FunnelMetrics(
        date=date.today().isoformat(),
        store_entries=entries,
        zone_engagements=len(zone_visitors),
        trial_area_visits=len(trial_visitors),
        billing_reaches=len(billing_visitors),
        conversion_rate=round(len(billing_visitors) / entries, 4),
        zone_engagement_rate=round(len(zone_visitors) / entries, 4),
        trial_rate=round(len(trial_visitors) / max(len(zone_visitors), 1), 4),
    )


def _compute_zone_dwells(events: List[PersonEvent]) -> List[ZoneDwellMetrics]:
    zone_enter: dict = defaultdict(dict)
    zone_dwells: dict = defaultdict(list)
    zone_visitors: dict = defaultdict(set)

    for e in events:
        if e.zone_id is None or e.is_staff:
            continue
        if e.event_type == EventType.ZONE_ENTER:
            zone_enter[e.zone_id][e.track_id] = e.timestamp
            zone_visitors[e.zone_id].add(e.track_id)
        elif e.event_type == EventType.ZONE_EXIT:
            if e.track_id in zone_enter.get(e.zone_id, {}):
                dt = (e.timestamp - zone_enter[e.zone_id][e.track_id]).total_seconds()
                zone_dwells[e.zone_id].append(dt)

    visit_counts = {z: len(zone_visitors.get(z, set())) for z in ZONE_NAMES}
    max_visits = max(visit_counts.values()) if visit_counts else 0
    if max_visits == 0:
        max_visits = 1

    results = []
    for zone_id, zone_name in ZONE_NAMES.items():
        dwell_list = zone_dwells.get(zone_id, [])
        visits = visit_counts.get(zone_id, 0)
        results.append(ZoneDwellMetrics(
            zone_id=zone_id,
            zone_name=zone_name,
            avg_dwell_seconds=round(sum(dwell_list) / len(dwell_list), 1) if dwell_list else 0.0,
            total_visits=visits,
            unique_visitors=len(zone_visitors.get(zone_id, set())),
            hotspot_score=round(visits / max_visits, 3),
        ))
    return results


async def build_metrics_response(state: StateManager, store_id: str) -> MetricsResponse:
    events = await state.get_events()
    anomalies = await state.get_anomalies()
    pipeline_status = await state.get_pipeline_status()

    traffic = _compute_traffic(events)
    funnel  = _compute_funnel(events, traffic)
    zones   = _compute_zone_dwells(events)

    return MetricsResponse(
        store_id=store_id,
        generated_at=datetime.utcnow(),
        traffic=traffic,
        funnel=funnel,
        zone_dwells=zones,
        anomalies=anomalies,
        pipeline_status=pipeline_status,
    )