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
from app.common.errors import conflict, not_found

logger = logging.getLogger(__name__)


async def monitor_stale_email_deliveries(stop_event: asyncio.Event) -> None:
    if not settings.email_queue_enabled:
        return
    while not stop_event.is_set():
        try:
            # 운영 SQS는 visibility timeout와 DLQ가 재시도를 소유한다. FastAPI가
            # SQS 대기 건을 임의로 FAILED 처리하면 재전달과 상태가 경합하므로,
            # 로컬 Redis에서만 stale 종결을 수행한다.
            if settings.email_queue_backend.lower() != "sqs":
                await _close_stale_deliveries()
            await purge_expired_recipient_data()
        except Exception:
            logger.exception("email stale delivery monitor failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=settings.email_retry_poll_interval_seconds)
        except asyncio.TimeoutError:
            continue


async def purge_expired_recipient_data() -> int:
    """종료된 발송 이력에서 보존기간이 지난 이메일 주소를 파기한다."""
    if settings.email_recipient_retention_days <= 0:
        return 0

    cutoff = utcnow() - timedelta(days=settings.email_recipient_retention_days)
    terminal_statuses = [
        EmailDeliveryStatus.SENT.value,
        EmailDeliveryStatus.DELIVERED.value,
        EmailDeliveryStatus.FAILED.value,
        EmailDeliveryStatus.BOUNCED.value,
        EmailDeliveryStatus.COMPLAINT.value,
    ]
    redacted = "[REDACTED]"
    async with AsyncSessionLocal() as db:
        deliveries = (await db.scalars(
            select(EmailDelivery).where(
                EmailDelivery.status.in_(terminal_statuses),
                EmailDelivery.updated_at < cutoff,
                EmailDelivery.recipient != redacted,
            )
        )).all()
        for delivery in deliveries:
            delivery.recipient = redacted
            delivery.recipient_normalized = redacted
            delivery.updated_at = utcnow()
        if deliveries:
            await db.commit()
            logger.info("Redacted %d expired email recipients", len(deliveries))
        return len(deliveries)


async def close_dlq_delivery(db, delivery_id: str) -> EmailDelivery:
    """DLQ에서 수동 확인한 발송 건을 최종 실패로 종결한다."""
    delivery = await db.scalar(
        select(EmailDelivery).where(EmailDelivery.delivery_id == delivery_id)
    )
    if delivery is None:
        raise not_found("EMAIL_DELIVERY_NOT_FOUND", "이메일 발송 요청을 찾을 수 없습니다.")
    if delivery.status not in {
        EmailDeliveryStatus.QUEUED.value,
        EmailDeliveryStatus.SENDING.value,
    }:
        raise conflict("EMAIL_DELIVERY_ALREADY_TERMINAL", "이미 종결된 이메일 발송 요청입니다.")

    delivery.status = EmailDeliveryStatus.FAILED.value
    delivery.failure_code = "DLQ_MANUAL_CLOSE"
    delivery.next_retry_at = None
    delivery.updated_at = utcnow()
    await db.commit()
    await db.refresh(delivery)
    return delivery


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
