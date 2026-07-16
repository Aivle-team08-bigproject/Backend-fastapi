from datetime import datetime, timezone


def utcnow() -> datetime:
    """타임존 정보가 없는(naive) UTC 기준 현재 시각을 반환한다.

    SQLite는 datetime의 타임존 정보를 저장/복원하지 못해서, tz-aware datetime을 저장했다가
    다시 읽으면 naive datetime으로 돌아온다 (Postgres 등 다른 백엔드는 다를 수 있음). 이 차이
    때문에 "can't compare offset-naive and offset-aware datetimes" 에러가 나기 쉽다.

    그래서 이 앱은 "DB에 저장되고 비교되는 모든 datetime은 UTC 기준의 naive datetime"이라는
    규약을 정하고, datetime.now(timezone.utc)를 직접 쓰지 않고 항상 이 함수를 통해서만
    현재 시각을 얻는다.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
