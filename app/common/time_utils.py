from datetime import datetime, timezone


def utcnow() -> datetime:
    """타임존 정보가 없는(naive) UTC 기준 현재 시각을 반환한다.

    tz-aware datetime과 naive datetime을 섞어 비교하면
    "can't compare offset-naive and offset-aware datetimes" 에러가 난다. 컬럼 타입이나
    드라이버(asyncpg/psycopg2)에 따라 읽어온 값이 naive가 되는 경우도 있어, 혼용을 막기 위해
    이 앱은 "DB에 저장되고 비교되는 모든 datetime은 UTC 기준의 naive datetime"이라는 규약을
    둔다. datetime.now(timezone.utc)를 직접 쓰지 않고 항상 이 함수를 통해서만 현재 시각을 얻는다.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
