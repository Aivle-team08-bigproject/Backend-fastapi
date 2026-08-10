"""Spring 발송 결과를 FastAPI의 이메일 상태 정본에 반영하는 worker."""

import asyncio
import json
import logging
from datetime import datetime, timezone

from redis.asyncio import Redis
from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.domains.pipeline.model import EmailDelivery, EmailDeliveryStatus

logger = logging.getLogger(__name__)


async def consume_email_result_queue(stop_event: asyncio.Event) -> None:
    """결과 큐를 소비한다. 큐가 비활성화된 환경에서는 실행하지 않는다."""
    if not settings.email_queue_enabled:
        return
    redis = Redis.from_url(settings.worker_status_redis_url, decode_responses=True)
    try:
        while not stop_event.is_set():
            item = await redis.blpop(settings.email_result_queue_key, timeout=1)
            if item is None:
                continue
            _, payload = item
            await _apply_result(payload)
    except asyncio.CancelledError:
        raise
    finally:
        await redis.aclose()


async def _apply_result(payload: str) -> None:
    try:
        result = json.loads(payload)
        delivery_id = str(result["delivery_id"])
        status = str(result["status"]).upper()
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        logger.warning("Ignoring malformed email result message")
        return

    allowed = {item.value for item in EmailDeliveryStatus}
    if status not in allowed:
        logger.warning("Ignoring unknown email delivery status=%s", status)
        return

    async with AsyncSessionLocal() as db:
        delivery = await db.scalar(select(EmailDelivery).where(EmailDelivery.delivery_id == delivery_id))
        if delivery is None:
            logger.warning("Email delivery not found delivery_id=%s", delivery_id)
            return
        delivery.status = status
        delivery.provider_message_id = result.get("provider_message_id")
        delivery.failure_code = result.get("failure_code")
        delivery.updated_at = datetime.now(timezone.utc)
        if status == EmailDeliveryStatus.DELIVERED.value:
            delivery.delivered_at = delivery.updated_at
        await db.commit()
