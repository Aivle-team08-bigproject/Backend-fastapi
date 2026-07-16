import logging
import uuid

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.domains.auth.service.session_activity_service import (
    touch_session_if_stale,
)

logger = logging.getLogger(__name__)


class SessionActivityMiddleware(BaseHTTPMiddleware):

    BUSINESS_PATH_PREFIXES = (
        "/api/admin/",
        "/api/requests/",
        "/api/tasks/",
        "/api/stats/",
    )

    async def dispatch(
        self,
        request: Request,
        call_next,
    ) -> Response:
        response = await call_next(request)

        if not self._is_activity_request(request, response):
            return response

        session_id = getattr(
            request.state,
            "session_id",
            None,
        )
        last_seen_at = getattr(
            request.state,
            "session_last_seen_at",
            None,
        )

        if session_id is None or last_seen_at is None:
            return response

        try:
            await touch_session_if_stale(
                session_id=uuid.UUID(str(session_id)),
                observed_last_seen_at=last_seen_at,
            )
        except Exception:
            logger.exception(
                "세션 활동 시간 갱신 실패: session_id=%s",
                session_id,
            )

        return response

    def _is_activity_request(
        self,
        request: Request,
        response: Response,
    ) -> bool:
        if not 200 <= response.status_code < 300:
            return False

        if request.headers.get(
            "X-User-Activity",
            "",
        ).strip().lower() != "true":
            return False

        return request.url.path.startswith(
            self.BUSINESS_PATH_PREFIXES
        )