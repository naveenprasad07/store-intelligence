
import os
import json, redis, uuid
from datetime import datetime
from dotenv import load_env

load_dotenv()

UPSTASH_URL = os.getenv("UPSTASH_REDIS_URL")
METRICS_FILE = "./metrics_133607.json"  # your downloaded file

r = redis.from_url(UPSTASH_URL)

with open(METRICS_FILE) as f:
    data = json.load(f)

traffic = data['traffic']
funnel  = data['funnel']
zones   = data['zone_dwells']

from app.schemas import PersonEvent, EventType
from app.state import StateManager
import asyncio

async def push():
    state = StateManager(UPSTASH_URL)
    await state.connect()

    today = datetime.utcnow().date().isoformat()

    # Push entry events
    for i in range(traffic['total_entries']):
        event = {
            "event_id": str(uuid.uuid4()),
            "event_type": "entry",
            "track_id": i + 1,
            "camera_id": "cam_01",
            "zone_id": "entry",
            "timestamp": f"{today}T10:{i:02d}:00",
            "confidence": 0.9,
            "is_staff": False,
            "session_id": str(uuid.uuid4())
        }
        from app.schemas import PersonEvent
        await state.push_event(PersonEvent(**event))

    # Push billing event
    billing_event = {
        "event_id": str(uuid.uuid4()),
        "event_type": "zone_enter",
        "track_id": 1,
        "camera_id": "cam_05",
        "zone_id": "billing",
        "timestamp": f"{today}T11:00:00",
        "confidence": 0.9,
        "is_staff": False,
        "session_id": str(uuid.uuid4())
    }
    await state.push_event(PersonEvent(**billing_event))

    await state.set_occupancy(traffic['current_occupancy'])
    await state.set_pipeline_status("completed")

    print("Done! Data pushed to Upstash.")

asyncio.run(push())
