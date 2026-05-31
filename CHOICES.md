# CHOICES.md — Engineering Trade-offs & Decisions

## Purplle Tech Challenge 2026

---

## 1. Model Choice: YOLOv8n vs. larger variants

**Decision**: YOLOv8n (nano)

**Reasoning**: The primary constraint is that this runs on commodity hardware with no guarantee of a GPU. YOLOv8n runs at ~80 FPS on a modern CPU for 640×480 input, which is well above the ~25 FPS of typical CCTV footage. Larger models (YOLOv8m, YOLOv8l) give ~2–5% mAP improvement on COCO person detection but run 3–10× slower.

**Trade-off accepted**: Slightly lower recall on very small/distant persons (e.g., at the back of a large store). Mitigated by: (a) multiple camera angles, (b) confidence threshold tuned down to 0.45, (c) DeepSORT's track smoothing.

**Swap path**: The model is loaded in one line (`YOLO("yolov8n.pt")`). Changing to `yolov8m.pt` requires no other code changes.

---

## 2. Tracker Choice: DeepSORT vs. ByteTrack vs. SORT

**Decision**: DeepSORT

**Reasoning**: DeepSORT uses appearance embeddings (re-ID features) in addition to Kalman-filter motion prediction. This is critical for:
- **Re-entry**: A customer who leaves and re-enters within a few seconds should ideally get the same track ID to avoid double-counting as a new visitor.
- **Occlusion behind shelving**: The appearance model helps re-associate tracks that briefly disappear.

ByteTrack is faster and competitive on benchmark datasets but has no appearance re-ID, making it more prone to ID switches in crowded retail environments.

**Trade-off accepted**: DeepSORT is slower (~20% overhead) and requires a pre-trained re-ID model. Acceptable given we're not CPU-constrained for 25 FPS input.

---

## 3. Entry/Exit via Virtual Line vs. Zone Boundary

**Decision**: Virtual horizontal line crossing

**Reasoning**: A full floor-plan homography (camera → top-down) requires calibration data (4+ known points per camera). Without calibration data, a virtual line approach with configurable `y_fraction` is far more robust and easier to validate visually.

**Trade-off accepted**: Less accurate for cameras not pointing perpendicular to the entry. Mitigated by loading line position from `store_layout.json` so it can be tuned per camera without code changes.

---

## 4. Staff Detection Heuristic

**Decision**: Dwell time > 30 minutes near billing zone = staff

**Reasoning**: Customers rarely stay in a single zone for 30+ minutes. No ML model is needed; a simple temporal rule works reliably. Staff badge detection (harder, requires labelled training data) would be more accurate but is out of scope.

**Trade-off accepted**: Staff who move around the store (not stationed at billing) will be counted as visitors for the first 30 minutes. Acceptable error margin for conversion rate calculations.

---

## 5. State Storage: Redis vs. PostgreSQL vs. In-Memory

**Decision**: Redis

**Reasoning**:
- Redis lists (`RPUSH` / `LRANGE`) are O(1) append and O(N) read — perfect for append-only event logs
- Redis pub/sub would allow live dashboard updates with zero extra infrastructure
- In-memory would be lost on restart; PostgreSQL is heavyweight for this hackathon scope
- 7-day TTL per key prevents unbounded growth

**Trade-off accepted**: Redis is not ACID-compliant. In a production system, events would be dual-written to a durable store (PostgreSQL / BigQuery). For this challenge, Redis is sufficient.

---

## 6. Synchronous Detection vs. Real-time Streaming

**Decision**: File-based batch processing (reads .mp4 files sequentially)

**Reasoning**: The challenge provides pre-recorded footage, not a live RTSP stream. Batch processing is simpler to implement correctly and debug. The architecture is stream-ready: swapping `cv2.VideoCapture(file)` for `cv2.VideoCapture("rtsp://...")` requires no other changes.

**Trade-off accepted**: Metrics are not truly "live" during batch processing. The API still serves correct metrics at any point using events committed so far.

---

## 7. Zone Mapping: Spatial Grid vs. Homography

**Decision**: Spatial grid (bbox centroid mapped to store section by x/y fraction)

**Reasoning**: Full homography mapping requires a calibration image with known floor markers per camera. Without this, a configurable grid (loaded from `store_layout.json`) gives a reasonable approximation for 5 cameras covering the store.

**Known limitation**: Accuracy degrades for oblique camera angles. The correct solution for production would be: (1) take a reference frame, (2) mark 4 floor points, (3) compute homography, (4) project all bounding box feet-points to floor coordinates. This is straightforward to add as a calibration step.

---

## 8. What I Would Do Differently with More Time

1. **Homography calibration UI** — simple web tool to click 4 points on a reference frame and map to known store coordinates
2. **Re-ID across cameras** — link track_ids from cam_01 and cam_02 when the same person moves between zones covered by different cameras
3. **MTMCT (Multi-Target Multi-Camera Tracking)** — production-grade cross-camera de-duplication
4. **Anomaly ML** — replace rule-based anomaly detection with an LSTM trained on normal traffic patterns
5. **Live RTSP ingestion** — replace file reading with GStreamer pipeline for true real-time processing
