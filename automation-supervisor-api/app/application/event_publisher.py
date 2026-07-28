import json

import redis.asyncio as redis

from app.core.config import settings


async def publish_job_event(job_id: int, event: dict) -> None:
    """job_id 채널로 현재 상태를 쏜다.

    Redis는 휘발성 라이브 버스일 뿐이다 — 이 publish가 실패하거나 구독자가 없어도
    Postgres의 상태는 이미 커밋되어 있다. 호출 순서(PG commit -> publish)는 항상
    지켜야 한다: 커밋 전에 publish하면 구독자가 아직 존재하지 않는 상태를 보게 된다.
    """
    client = redis.from_url(settings.redis_url)
    try:
        await client.publish(f"events:job:{job_id}", json.dumps(event, default=str))
    finally:
        await client.aclose()
