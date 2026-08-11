import hmac

from fastapi import Header

from app.common.errors import unauthorized
from app.core.config import settings


def verify_internal_service_key(x_internal_service_key: str | None = Header(default=None)) -> None:
    """Spring 등 내부 서비스가 이 서버를 호출할 때만 통과시킨다. 직원 JWT와는 별개다."""
    if x_internal_service_key is None or not hmac.compare_digest(
        x_internal_service_key, settings.internal_service_key
    ):
        raise unauthorized("INVALID_INTERNAL_SERVICE_KEY", "내부 서비스 인증에 실패했습니다.")
