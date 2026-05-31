import json
import redis.asyncio as aioredis
from datetime import datetime, date
from typing import List, Optional
from app.schemas import PersonEvent, AnomalyEvent


class StateManager:
    """
    All pipeline state lives in Redis so the API and detector are decoupled.
    Key schema:
      events:YYYY-MM-DD        -> Redis list of serialized PersonEvent JSON
      occupancy:current        -> int (current people inside)
      tracks:active            -> hash  track_id -> {entry_time, zone, session_id}
      anomalies:YYYY-MM-DD     -> Redis list of AnomalyEvent JSON
      metrics:last_updated     -> ISO timestamp string
    """

    def __init__(self, redis_url: str):
        self.redis_url = redis_url
        self._client: Optional[aioredis.Redis] = None

    async def connect(self):
        self._client = aioredis.from_url(self.redis_url, decode_responses=True)
        await self._client.ping()

    async def close(self):
        if self._client:
            await self._client.close()

    async def push_event(self, event: PersonEvent):
        key = f"events:{event.timestamp.date().isoformat()}"
        await self._client.rpush(key, event.model_dump_json())
        await self._client.expire(key, 86400 * 7)  # keep 7 days

    async def get_events(self, for_date: Optional[date] = None) -> List[PersonEvent]:
        d = (for_date or date.today()).isoformat()
        raw = await self._client.lrange(f"events:{d}", 0, -1)
        return [PersonEvent.model_validate_json(r) for r in raw]

    async def push_anomaly(self, anomaly: AnomalyEvent):
        key = f"anomalies:{anomaly.detected_at.date().isoformat()}"
        await self._client.rpush(key, anomaly.model_dump_json())
        await self._client.expire(key, 86400 * 7)

    async def get_anomalies(self, for_date: Optional[date] = None) -> List[AnomalyEvent]:
        d = (for_date or date.today()).isoformat()
        raw = await self._client.lrange(f"anomalies:{d}", 0, -1)
        return [AnomalyEvent.model_validate_json(r) for r in raw]

    async def set_occupancy(self, count: int):
        await self._client.set("occupancy:current", count)

    async def get_occupancy(self) -> int:
        v = await self._client.get("occupancy:current")
        return int(v) if v else 0

    async def set_active_track(self, track_id: int, data: dict):
        await self._client.hset("tracks:active", str(track_id), json.dumps(data))

    async def remove_active_track(self, track_id: int):
        await self._client.hdel("tracks:active", str(track_id))

    async def get_active_tracks(self) -> dict:
        raw = await self._client.hgetall("tracks:active")
        return {int(k): json.loads(v) for k, v in raw.items()}

    async def set_pipeline_status(self, status: str):
        await self._client.set("pipeline:status", status)

    async def get_pipeline_status(self) -> str:
        v = await self._client.get("pipeline:status")
        return v or "idle"
