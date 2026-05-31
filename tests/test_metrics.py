"""
Tests for Store Intelligence pipeline.
Run: pytest tests/ -v
"""

import pytest
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock
import uuid

from app.schemas import PersonEvent, EventType
from app.metrics import _compute_traffic, _compute_funnel, _compute_zone_dwells


def make_event(event_type: EventType, track_id: int, timestamp: datetime,
               zone_id=None, is_staff=False) -> PersonEvent:
    return PersonEvent(
        event_id=str(uuid.uuid4()),
        event_type=event_type,
        track_id=track_id,
        camera_id="cam_01",
        zone_id=zone_id,
        timestamp=timestamp,
        confidence=0.9,
        is_staff=is_staff,
        session_id=str(uuid.uuid4()),
    )


BASE_TIME = datetime(2026, 4, 10, 12, 0, 0)


# ─── Traffic metric tests ────────────────────────────────────────────────────

class TestTrafficMetrics:
    def test_basic_counts(self):
        events = [
            make_event(EventType.ENTRY, 1, BASE_TIME),
            make_event(EventType.ENTRY, 2, BASE_TIME),
            make_event(EventType.EXIT,  1, BASE_TIME + timedelta(minutes=30)),
        ]
        t = _compute_traffic(events)
        assert t.total_entries == 2
        assert t.total_exits == 1
        assert t.unique_visitors == 2
        assert t.current_occupancy == 1

    def test_staff_excluded_from_visitor_count(self):
        events = [
            make_event(EventType.ENTRY, 1, BASE_TIME, is_staff=False),
            make_event(EventType.ENTRY, 2, BASE_TIME, is_staff=True),   # staff
        ]
        t = _compute_traffic(events)
        assert t.unique_visitors == 1
        assert t.staff_count == 1

    def test_reentry_counted_as_one_session(self):
        """Same track_id entering twice → counted once in unique_visitors."""
        events = [
            make_event(EventType.ENTRY, 1, BASE_TIME),
            make_event(EventType.EXIT,  1, BASE_TIME + timedelta(minutes=5)),
            make_event(EventType.ENTRY, 1, BASE_TIME + timedelta(minutes=10)),
        ]
        t = _compute_traffic(events)
        assert t.unique_visitors == 1

    def test_avg_dwell_calculation(self):
        events = [
            make_event(EventType.ENTRY, 1, BASE_TIME),
            make_event(EventType.EXIT,  1, BASE_TIME + timedelta(minutes=20)),
            make_event(EventType.ENTRY, 2, BASE_TIME),
            make_event(EventType.EXIT,  2, BASE_TIME + timedelta(minutes=40)),
        ]
        t = _compute_traffic(events)
        assert t.avg_dwell_minutes == pytest.approx(30.0, abs=0.1)

    def test_zero_traffic(self):
        t = _compute_traffic([])
        assert t.total_entries == 0
        assert t.current_occupancy == 0
        assert t.avg_dwell_minutes == 0.0


# ─── Funnel metric tests ─────────────────────────────────────────────────────

class TestFunnelMetrics:
    def _base_events(self):
        events = [
            make_event(EventType.ENTRY,      1, BASE_TIME),
            make_event(EventType.ENTRY,      2, BASE_TIME),
            make_event(EventType.ENTRY,      3, BASE_TIME),
            make_event(EventType.ZONE_ENTER, 1, BASE_TIME, zone_id="skin"),
            make_event(EventType.ZONE_ENTER, 2, BASE_TIME, zone_id="makeup"),
            make_event(EventType.ZONE_ENTER, 1, BASE_TIME, zone_id="trial_area"),
            make_event(EventType.ZONE_ENTER, 1, BASE_TIME, zone_id="billing"),
        ]
        return events

    def test_conversion_rate(self):
        events = self._base_events()
        traffic = _compute_traffic(events)
        funnel = _compute_funnel(events, traffic)
        # 1 reached billing out of 3 entries
        assert funnel.conversion_rate == pytest.approx(1 / 3, abs=0.01)

    def test_no_double_counting(self):
        """Same person entering billing zone twice → counted once."""
        events = [
            make_event(EventType.ENTRY,      1, BASE_TIME),
            make_event(EventType.ZONE_ENTER, 1, BASE_TIME, zone_id="billing"),
            make_event(EventType.ZONE_ENTER, 1, BASE_TIME + timedelta(minutes=5), zone_id="billing"),
        ]
        traffic = _compute_traffic(events)
        funnel = _compute_funnel(events, traffic)
        assert funnel.billing_reaches == 1

    def test_staff_excluded_from_funnel(self):
        events = [
            make_event(EventType.ENTRY,      1, BASE_TIME, is_staff=False),
            make_event(EventType.ENTRY,      2, BASE_TIME, is_staff=True),
            make_event(EventType.ZONE_ENTER, 2, BASE_TIME, zone_id="billing", is_staff=True),
        ]
        traffic = _compute_traffic(events)
        funnel = _compute_funnel(events, traffic)
        assert funnel.billing_reaches == 0


# ─── Zone dwell tests ────────────────────────────────────────────────────────

class TestZoneDwellMetrics:
    def test_dwell_calculation(self):
        events = [
            make_event(EventType.ZONE_ENTER, 1, BASE_TIME,                      zone_id="skin"),
            make_event(EventType.ZONE_EXIT,  1, BASE_TIME + timedelta(seconds=90), zone_id="skin"),
        ]
        zones = _compute_zone_dwells(events)
        skin = next(z for z in zones if z.zone_id == "skin")
        assert skin.avg_dwell_seconds == pytest.approx(90.0, abs=1)
        assert skin.total_visits == 1

    def test_hotspot_score_normalization(self):
        events = [
            make_event(EventType.ZONE_ENTER, 1, BASE_TIME, zone_id="makeup"),
            make_event(EventType.ZONE_ENTER, 2, BASE_TIME, zone_id="makeup"),
            make_event(EventType.ZONE_ENTER, 3, BASE_TIME, zone_id="makeup"),
            make_event(EventType.ZONE_ENTER, 1, BASE_TIME, zone_id="skin"),
        ]
        zones = _compute_zone_dwells(events)
        makeup = next(z for z in zones if z.zone_id == "makeup")
        skin   = next(z for z in zones if z.zone_id == "skin")
        assert makeup.hotspot_score == pytest.approx(1.0)
        assert skin.hotspot_score < 1.0
