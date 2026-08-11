"""FastAPI → Spring 이메일 요청 큐 adapter."""

import json
import logging
import asyncio
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
    # 메일 본문에 채울 안내 문구용 컨텍스트. 값이 없으면 빈 문자열로 내려온다.
    request_no: str
    request_title: str
    client_company_name: str
    owner_name: str
    owner_email: str
    # delivery_type == FINAL_ARTIFACT일 때만 채워진다. Spring이 이 storage_key로
    # 자기 S3 자격증명으로 직접 presigned URL을 새로 만든다(FastAPI가 만든 URL을
    # 그대로 넘기면 메일이 열릴 때쯤 만료됐을 수 있어서, 발송 시점에 다시 서명한다).
    artifact_storage_key: str
    artifact_filename: str
    artifact_mime_type: str


async def publish_email_delivery(message: EmailDeliveryQueueMessage) -> bool:
    """이메일 요청 큐에 발행한다.

    로컬은 Redis 리스트를 사용하고, 운영에서는 SQS를 선택한다.
    """
    if not settings.email_queue_enabled:
        logger.info("Email queue disabled; delivery remains QUEUED: %s", message.delivery_id)
        return False
    payload = json.dumps(asdict(message), ensure_ascii=False)
    if settings.email_queue_backend.lower() == "sqs":
        if not settings.email_request_queue_url:
            raise RuntimeError("EMAIL_REQUEST_QUEUE_URL is required for SQS backend")

        def _send() -> None:
            import boto3

            boto3.client("sqs", region_name=settings.aws_region).send_message(
                QueueUrl=settings.email_request_queue_url,
                MessageBody=payload,
            )

        await asyncio.to_thread(_send)
        return True

    redis = Redis.from_url(settings.worker_status_redis_url, decode_responses=True)
    try:
        await redis.rpush(settings.email_request_queue_key, payload)
        return True
    finally:
        await redis.aclose()
