"""엔진 커넥션 풀 설정을 한 곳에서 만든다.

엔진이 3개(app_svc 2, agent_svc 1)라 설정을 각 파일에 흩어두면 한 곳만 고치는
실수가 나기 쉽다. 여기서 만들어 셋이 같이 쓴다.
"""

from typing import Any

from sqlalchemy.pool import NullPool

from app.core.config import settings


def engine_kwargs() -> dict[str, Any]:
    """create_async_engine 에 넘길 풀 관련 인자를 만든다.

    NullPool 과 pool_size/max_overflow 는 동시에 지정할 수 없다(SQLAlchemy 가
    TypeError 를 낸다). 그래서 둘 중 하나만 반환한다.
    """
    if settings.db_use_null_pool:
        # Celery prefork 워커용. 태스크마다 커넥션을 열고 끝나면 닫는다.
        # 부모 프로세스가 커넥션을 연 채로 fork 되면 자식들이 같은 소켓을
        # 물려받아 요청·응답이 뒤섞이는데, 붙들지 않으면 그 상황 자체가 없다.
        return {"poolclass": NullPool}

    return {
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        "pool_timeout": settings.db_pool_timeout,
        "pool_recycle": settings.db_pool_recycle,
    }
