from datetime import datetime, timezone


def utcnow() -> datetime:
    """UTC 기준의 timezone-aware 현재 시각을 반환한다."""
    return datetime.now(timezone.utc)


def as_utc(value: datetime) -> datetime:
    """DB 드라이버가 반환한 naive/aware 시각을 UTC aware로 통일한다."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)
