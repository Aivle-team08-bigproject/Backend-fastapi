"""이메일 큐 장애 감시 worker.

현재 DB 계약은 스냅샷 원문을 저장하지 않으므로 자동 재발행은 하지 않는다.
재발행 가능한 스냅샷 저장 계약이 확정되기 전까지 장기 정체 건을 FAILED로
종결해 화면에 영원히 QUEUED/SENDING으로 남지 않게 한다.
"""

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import select

from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.domains.pipeline.model import EmailDelivery, EmailDeliveryStatus
from app.common.time_utils import utcnow

logger = logging.getLogger(__name__)


async def monitor_stale_email_deliveries(stop_event: asyncio.Event) -> None:
    if not settings.email_queue_enabled:
        return
    # 운영 SQS는 visibility timeout와 DLQ가 재시도를 소유한다. FastAPI가
    # 여기서 임의로 FAILED 처리하면 SQS 재전달 중인 메시지와 상태가 경합한다.
    if settings.email_queue_backend.lower() == "sqs":
        return
    while not stop_event.is_set():
        try:
            await _close_stale_deliveries()
        except Exception:
            logger.exception("email stale delivery monitor failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.email_retry_poll_interval_seconds)
        except asyncio.TimeoutError:
            continue


async def _close_stale_deliveries() -> None:
    cutoff = utcnow() - timedelta(seconds=settings.email_stale_after_seconds)
    async with AsyncSessionLocal() as db:
        deliveries = (await db.scalars(
            select(EmailDelivery).where(
                EmailDelivery.status.in_([
                    EmailDeliveryStatus.QUEUED.value,
                    EmailDeliveryStatus.SENDING.value,
                ]),
                EmailDelivery.updated_at < cutoff,
            )
        )).all()
        for delivery in deliveries:
            delivery.status = EmailDeliveryStatus.FAILED.value
            delivery.failure_code = "QUEUE_STALE_REQUIRES_RETRY_PAYLOAD"
            delivery.next_retry_at = None
        if deliveries:
            await db.commit()
            logger.warning("Closed %d stale email deliveries", len(deliveries))
