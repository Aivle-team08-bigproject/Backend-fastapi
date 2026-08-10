"""FastAPI → Spring 이메일 요청 큐 adapter."""

import json
import logging
from dataclasses import asdict, dataclass

from redis.asyncio import Redis

from app.core.config import settings

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class EmailDeliveryQueueMessage:
    delivery_id: str
    idempotency_key: str
    run_id: int
    stage_attempt_no: int
    delivery_type: str
    recipient: str
    template_version: str
    sample_columns: list[dict]
    sample_rows: list[dict]
    sample_metadata: dict
    sample_sha256: str


async def publish_email_delivery(message: EmailDeliveryQueueMessage) -> bool:
    """로컬 Redis 큐 발행 adapter. 운영 SQS adapter가 같은 계약을 구현한다."""
    if not settings.email_queue_enabled:
        logger.info("Email queue disabled; delivery remains QUEUED: %s", message.delivery_id)
        return False
    redis = Redis.from_url(settings.worker_status_redis_url, decode_responses=True)
    try:
        await redis.rpush(settings.email_request_queue_key, json.dumps(asdict(message), ensure_ascii=False))
        return True
    finally:
        await redis.aclose()
