from datetime import datetime, timedelta
from uuid import UUID

from sqlalchemy import update

from app.common.time_utils import as_utc, utcnow
from app.core.config import settings
from app.db.session import AsyncSessionLocal
from app.domains.auth.model.session_model import LoginSession


async def touch_session_if_stale(
    session_id: UUID,
    observed_last_seen_at: datetime,
) -> bool:
    """
    마지막 활동시간 갱신 후 설정된 시간이 지났을 때만
    login_sessions.last_seen_at을 갱신한다.

    반환값:
    - True: 실제 UPDATE가 실행됨
    - False: 갱신 간격이 지나지 않았거나 세션이 비활성 상태
    """

    now = utcnow()

    touch_interval = timedelta(
        seconds=settings.session_activity_touch_interval_seconds
    )
    threshold = now - touch_interval

    # 인증 과정에서 확인한 활동 시간이 아직 충분히 최근이면
    # DB 쿼리 자체를 실행하지 않는다.
    if as_utc(observed_last_seen_at) >= threshold:
        return False

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            update(LoginSession)
            .where(
                LoginSession.id == session_id,
                LoginSession.revoked_at.is_(None),

                # 다른 요청이 먼저 갱신했다면 조건이 일치하지 않음
                LoginSession.last_seen_at < threshold,
            )
            .values(last_seen_at=now)
        )

        updated_count = result.rowcount or 0

        if updated_count == 0:
            await db.rollback()
            return False

        await db.commit()
        return True
