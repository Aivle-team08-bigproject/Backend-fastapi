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
    if settings.email_queue_backend.lower() == "sqs":
        await _consume_sqs_results(stop_event)
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


async def _consume_sqs_results(stop_event: asyncio.Event) -> None:
    """SQS 결과를 반영하고 DB 커밋 후에만 메시지를 삭제한다."""
    if not settings.email_result_queue_url:
        raise RuntimeError("EMAIL_RESULT_QUEUE_URL is required for SQS backend")

    import boto3

    client = boto3.client("sqs", region_name=settings.aws_region)
    while not stop_event.is_set():
        response = await asyncio.to_thread(
            client.receive_message,
            QueueUrl=settings.email_result_queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=settings.email_sqs_wait_seconds,
            VisibilityTimeout=settings.email_sqs_visibility_timeout_seconds,
        )
        for message in response.get("Messages", []):
            await _apply_result(message["Body"])
            await asyncio.to_thread(
                client.delete_message,
                QueueUrl=settings.email_result_queue_url,
                ReceiptHandle=message["ReceiptHandle"],
            )


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

    await _publish_status(delivery_id, status, result.get("provider_message_id"), result.get("failure_code"))


async def _publish_status(
    delivery_id: str,
    status: str,
    provider_message_id: str | None,
    failure_code: str | None,
) -> None:
    """DB 반영 직후 SSE 채널에 발행한다. 실패해도 폴백(재조회)이 가능하므로 무시한다."""
    redis = Redis.from_url(settings.worker_status_redis_url, decode_responses=True)
    try:
        await redis.publish(
            settings.email_delivery_sse_channel,
            json.dumps(
                {
                    "delivery_id": delivery_id,
                    "status": status,
                    "provider_message_id": provider_message_id,
                    "failure_code": failure_code,
                },
                ensure_ascii=False,
            ),
        )
    except Exception:
        logger.warning("Failed to publish email delivery status delivery_id=%s", delivery_id, exc_info=True)
    finally:
        await redis.aclose()
