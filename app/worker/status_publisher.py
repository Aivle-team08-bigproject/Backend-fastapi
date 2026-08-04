"""화면 갱신용 상태 발행 — DB 쓰기가 끝난 뒤에만 호출된다.

여기서 나가는 Redis 메시지는 프론트 화면을 갱신하기 위한 것이다(FastAPI SSE가 구독해서
브라우저로 흘려보낸다). 상태의 정본은 PostgreSQL이고, 이 발행이 실패해도 상태는 유실되지
않는다 — 다음 이벤트나 SSE 최초 접속 시 DB에서 현재 상태를 읽어 복구된다.

`latest` 키는 SSE 접속 직후 첫 프레임을 즉시 내려주기 위한 캐시다.
"""

import logging

from redis import Redis

from app.core.config import settings
from app.worker.status_event import PipelineStatusEvent
from app.domains.pipeline.failure import public_step_metadata


logger = logging.getLogger(__name__)

_redis_client = Redis.from_url(settings.worker_status_redis_url, decode_responses=True)


def publish_to_screen(event: PipelineStatusEvent) -> None:
    # result에는 실패 후보 계획이나 성공 산출물이 포함될 수 있으므로 SSE에는 내보내지 않는다.
    public_event = event.model_copy(
        update={"step_metadata": public_step_metadata(event.step_metadata)}
    )
    payload = public_event.model_dump_json(exclude={"result"})
    latest_key = f"{settings.worker_status_key_prefix}:{event.run_id}"
    try:
        with _redis_client.pipeline() as pipe:
            pipe.set(latest_key, payload, ex=settings.worker_status_ttl_seconds)
            pipe.publish(settings.worker_status_sse_channel, payload)
            pipe.execute()
    except Exception:
        # 화면 갱신 실패는 작업 실패가 아니다 — 상태는 이미 DB에 있다.
        logger.exception("Failed to publish pipeline status for screen update run_id=%s", event.run_id)
