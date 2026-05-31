"""
Detection Pipeline
==================
Processes each CCTV .mp4 file, runs YOLO v8 person detection + DeepSORT tracking,
and classifies events (entry / exit / zone transitions).

Key design decisions (see CHOICES.md):
  - YOLOv8n (nano) for speed; swappable to YOLOv8m for accuracy
  - DeepSORT for re-ID across occlusions and re-entries
  - Virtual line crossing (configurable y-fraction) for entry/exit
  - Staff detection: persons staying > STAFF_DWELL_THRESHOLD seconds near billing
  - Per-camera processing → events pushed to API /ingest/event
"""

import os
import uuid
import json
import time
import logging
import asyncio
from datetime import datetime
from pathlib import Path
from typing import Dict, Tuple

import cv2
import numpy as np
import httpx

log = logging.getLogger(__name__)

API_BASE   = os.getenv("API_URL", "http://api:8000")
VIDEO_DIR  = Path(os.getenv("VIDEO_DIR", "./videos"))
LAYOUT_PATH = Path(os.getenv("LAYOUT_PATH", "./store_layout.json"))

# Tuneable constants
STAFF_DWELL_THRESHOLD = 60 * 30     # seconds: >30 min near billing → staff
MIN_DETECTION_CONF    = 0.45
ENTRY_COOLDOWN_SEC    = 10           # ignore re-entry within 10 s (tail-gating)
MAX_TRACK_AGE         = 30           # frames before track considered lost


def load_layout() -> dict:
    if LAYOUT_PATH.exists():
        return json.loads(LAYOUT_PATH.read_text())
    return {"entry_line_y_fraction": 0.15, "exit_line_y_fraction": 0.85}


def get_zone_for_bbox(bbox: list, frame_w: int, frame_h: int, zones_cfg: list) -> str | None:
    """
    Simple spatial mapping: divide frame into grid based on zone positions.
    In production this would use the actual store floor-plan homography.
    """
    cx = (bbox[0] + bbox[2]) / 2 / frame_w
    cy = (bbox[1] + bbox[3]) / 2 / frame_h

    if cy < 0.2:
        return "entry"
    if cy > 0.8:
        return "billing"
    if cx < 0.33:
        return "skin" if cy < 0.5 else "hair"
    if cx < 0.66:
        return "makeup" if cy < 0.5 else "bath_body"
    return "fragrance" if cy < 0.5 else "personal_care"


class TrackState:
    def __init__(self, track_id: int, camera_id: str):
        self.track_id   = track_id
        self.camera_id  = camera_id
        self.session_id = str(uuid.uuid4())
        self.entered    = False
        self.exited     = False
        self.is_staff   = False
        self.entry_time: datetime | None = None
        self.last_zone: str | None = None
        self.zone_enter_time: datetime | None = None
        self.last_seen  = time.time()
        self.billing_dwell = 0.0


async def post_event(client: httpx.AsyncClient, payload: dict):
    try:
        await client.post(f"{API_BASE}/ingest/event", json=payload, timeout=5)
    except Exception as e:
        log.warning("Event post failed: %s", e)


async def post_anomaly(client: httpx.AsyncClient, payload: dict):
    try:
        await client.post(f"{API_BASE}/ingest/anomaly", json=payload, timeout=5)
    except Exception as e:
        log.warning("Anomaly post failed: %s", e)


def make_event(track: TrackState, event_type: str, zone_id: str | None, bbox, confidence: float) -> dict:
    return {
        "event_id":   str(uuid.uuid4()),
        "event_type": event_type,
        "track_id":   track.track_id,
        "camera_id":  track.camera_id,
        "zone_id":    zone_id,
        "timestamp":  datetime.utcnow().isoformat(),
        "confidence": float(confidence),
        "is_staff":   track.is_staff,
        "bbox":       [float(v) for v in bbox] if bbox is not None else None,
        "session_id": track.session_id,
    }


async def process_video(video_path: Path, camera_id: str, layout: dict, client: httpx.AsyncClient):
    """Process one video file end-to-end."""
    try:
        from ultralytics import YOLO
        from deep_sort_realtime.deepsort_tracker import DeepSort
    except ImportError:
        log.error("ultralytics / deep_sort_realtime not installed")
        return

    log.info("Processing %s (camera: %s)", video_path.name, camera_id)

    model   = YOLO("yolov8n.pt")
    tracker = DeepSort(max_age=MAX_TRACK_AGE, n_init=3, nn_budget=100)

    cap = cv2.VideoCapture(str(video_path))
    if not cap.isOpened():
        log.error("Cannot open video %s", video_path)
        return

    fps        = cap.get(cv2.CAP_PROP_FPS) or 25
    frame_w    = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
    frame_h    = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
    entry_y    = int(layout.get("entry_line_y_fraction", 0.15) * frame_h)
    exit_y     = int(layout.get("exit_line_y_fraction",  0.85) * frame_h)

    tracks: Dict[int, TrackState] = {}
    frame_idx  = 0
    group_buffer: list = []  # for group-entry anomaly detection

    await client.post(f"{API_BASE}/ingest/anomaly",  # signal pipeline start
        json={"anomaly_id": str(uuid.uuid4()), "anomaly_type": "pipeline_start",
              "description": f"Pipeline started for {camera_id}",
              "severity": "low", "detected_at": datetime.utcnow().isoformat(),
              "camera_id": camera_id, "track_ids": []}, timeout=5)

    while True:
        ret, frame = cap.read()
        if not ret:
            break
        frame_idx += 1

        # Run detection every frame (drop to every 2nd for perf on weak hardware)
        results = model(frame, classes=[0], conf=MIN_DETECTION_CONF, verbose=False)
        dets = []
        for box in results[0].boxes:
            x1, y1, x2, y2 = box.xyxy[0].tolist()
            conf = float(box.conf[0])
            dets.append(([x1, y1, x2 - x1, y2 - y1], conf, "person"))

        active_tracks = tracker.update_tracks(dets, frame=frame)

        current_frame_ids = set()
        group_in_frame = 0

        for t in active_tracks:
            if not t.is_confirmed():
                continue
            tid  = t.track_id
            bbox = t.to_ltrb()  # [x1,y1,x2,y2]
            cy   = (bbox[1] + bbox[3]) / 2
            current_frame_ids.add(tid)

            if tid not in tracks:
                tracks[tid] = TrackState(tid, camera_id)

            state = tracks[tid]
            state.last_seen = time.time()

            # ── Entry detection ───────────────────────────────────────────
            if not state.entered and cy < entry_y:
                state.entered    = True
                state.entry_time = datetime.utcnow()
                await post_event(client, make_event(state, "entry", "entry", bbox, 0.85))
                group_in_frame += 1

            # ── Exit detection ────────────────────────────────────────────
            if state.entered and not state.exited and cy > exit_y:
                state.exited = True
                await post_event(client, make_event(state, "exit", "entry", bbox, 0.85))

            # ── Zone transition ───────────────────────────────────────────
            if state.entered and not state.exited:
                zone = get_zone_for_bbox(bbox, frame_w, frame_h, [])

                # Staff heuristic: billing dwell > threshold
                if zone == "billing":
                    state.billing_dwell += 1 / fps
                    if state.billing_dwell > STAFF_DWELL_THRESHOLD and not state.is_staff:
                        state.is_staff = True
                        log.info("Track %d flagged as staff", tid)

                if zone != state.last_zone:
                    if state.last_zone is not None:
                        await post_event(client, make_event(state, "zone_exit", state.last_zone, bbox, 0.75))
                    state.last_zone = zone
                    state.zone_enter_time = datetime.utcnow()
                    await post_event(client, make_event(state, "zone_enter", zone, bbox, 0.75))

        # ── Group entry anomaly (>=3 people enter together) ───────────────
        if group_in_frame >= 3:
            group_buffer.append(frame_idx)
            if len(group_buffer) == 1 or frame_idx - group_buffer[-2] < fps * 2:
                if len(group_buffer) >= 3:
                    await post_anomaly(client, {
                        "anomaly_id": str(uuid.uuid4()),
                        "anomaly_type": "group_entry",
                        "description": f"Group of {group_in_frame} people entering simultaneously",
                        "severity": "low",
                        "detected_at": datetime.utcnow().isoformat(),
                        "camera_id": camera_id,
                        "track_ids": list(current_frame_ids),
                    })
                    group_buffer.clear()
            else:
                group_buffer.clear()

        # ── Loitering anomaly: single person > 10 min in entry ───────────
        for tid, state in tracks.items():
            if (state.entered and not state.exited and state.last_zone == "entry"
                    and state.zone_enter_time):
                minutes = (datetime.utcnow() - state.zone_enter_time).total_seconds() / 60
                if minutes > 10:
                    await post_anomaly(client, {
                        "anomaly_id": str(uuid.uuid4()),
                        "anomaly_type": "loitering",
                        "description": f"Track {tid} loitering at entry for {minutes:.1f} min",
                        "severity": "medium",
                        "detected_at": datetime.utcnow().isoformat(),
                        "camera_id": camera_id,
                        "track_ids": [tid],
                    })
                    state.zone_enter_time = datetime.utcnow()  # reset to avoid spam

    cap.release()
    log.info("Finished %s: %d tracks seen", video_path.name, len(tracks))


async def run_pipeline():
    layout = load_layout()
    cameras = {c["id"]: c for c in layout.get("cameras", [])}

    video_files = sorted(VIDEO_DIR.glob("*.mp4"))
    if not video_files:
        log.warning("No .mp4 files found in %s — check VIDEO_DIR", VIDEO_DIR)
        return

    async with httpx.AsyncClient() as client:
        # Signal running
        await client.post(f"{API_BASE}/ingest/anomaly", json={
            "anomaly_id": str(uuid.uuid4()), "anomaly_type": "system",
            "description": "Detection pipeline starting", "severity": "low",
            "detected_at": datetime.utcnow().isoformat(), "camera_id": "system", "track_ids": [],
        }, timeout=5)

        for i, vf in enumerate(video_files):
            # Map video filename to camera id heuristically
            cam_id = f"cam_{i+1:02d}"
            for cid, cam in cameras.items():
                if cid in vf.stem or cam.get("location", "") in vf.stem:
                    cam_id = cid
                    break
            await process_video(vf, cam_id, layout, client)

    log.info("Pipeline complete.")


if __name__ == "__main__":
    logging.basicConfig(level=os.getenv("LOG_LEVEL", "INFO"))
    asyncio.run(run_pipeline())
