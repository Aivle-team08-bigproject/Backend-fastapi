"""DLP 검사 지점 결선용 헬퍼.

검사 지점이 세 곳(요구사항 제출 · HITL 검토 의견 · 가입 반려 사유)이라 같은 절차가
세 번 반복된다. 그 절차를 한 함수로 모은다.

    검사 → (BLOCK 이면 400) → (FLAG 이고 미확인이면 409) → 감사 기록

## 왜 감사 기록을 별도 세션에 쓰는가

`get_db` 는 명시적 commit 을 하지 않는다(`app/db/session.py`). 그래서 여기서
`DomainException` 을 던지면 요청 세션이 닫히면서 **막았다는 기록까지 함께
롤백된다.** BLOCK 을 막고도 흔적이 없으면 감사 요건을 못 지킨다.

호출부에서 `await db.commit()` 을 먼저 부르는 방법이 가장 간단하지만 택하지 않았다.
guard 가 남의 트랜잭션 경계를 침범하게 되고, 나중에 검사 위치가 한 줄 아래로 옮겨지면
아직 완성되지 않은 변경이 **조용히 부분 커밋**된다. 같은 이유로 파이프라인 관찰 로그도
`executor._record_agent_log_isolated` 가 세션을 따로 연다. 그 패턴을 따른다.

## 왜 BLOCK 의 source_id 는 NULL 인가

BLOCK 은 원본 행이 저장되지 않으므로 참조할 id 가 없다. 기록하려고 행을 먼저 만들면
**막으려던 값이 DB 에 들어가는** 모순이 생긴다. `detected_at` · `actor_employee_code` ·
`source_endpoint` 로 추적한다.
"""

from __future__ import annotations

import logging

from fastapi import status

from app.common.errors import DomainException
from app.common.pii import scan
from app.common.pii.model import PiiDetection
from app.common.pii.types import ScanResult
from app.db.session import AsyncSessionLocal

logger = logging.getLogger(__name__)


class PiiDetectedException(DomainException):
    """탐지 결과를 응답 본문에 함께 싣는 도메인 예외.

    `DomainException` 의 detail 은 code/message 뿐이라 프론트가 "무엇이 몇 건
    걸렸는지"를 보여줄 수 없다. detections 를 얹어 입력창 하이라이트까지 가능하게 한다.
    공용 `app/common/errors.py` 를 고치지 않는 이유는 다른 도메인에 영향을 주지 않기
    위해서다.
    """

    def __init__(self, status_code: int, code: str, message: str, result: ScanResult):
        super().__init__(status_code, code, message)
        self.detail.update(result.to_response())
        self.result = result


# action_taken 3값. severity 는 탐지기의 판정이고 이쪽은 실제로 일어난 일이다.
ACTION_REJECTED = "REJECTED"
ACTION_PROCEEDED_CONFIRMED = "PROCEEDED_CONFIRMED"
ACTION_RECORDED = "RECORDED"

BLOCK_MESSAGE = "요청 내용에 주민등록번호 등 민감한 개인정보가 포함되어 있습니다. 해당 부분을 지운 뒤 다시 제출해 주세요."
FLAG_MESSAGE = "요청 내용에 개인정보로 보이는 값이 있습니다. 확인 후 그대로 진행하려면 다시 제출해 주세요."


def client_ip(request) -> str | None:
    """요청 IP. 라우터 세 곳이 같은 줄을 반복하지 않도록 여기 둔다.

    `X-Forwarded-For` 는 보지 않는다. 신뢰할 프록시 목록을 정하지 않은 상태에서
    그 헤더를 믿으면 감사 기록의 IP 를 요청자가 마음대로 위조할 수 있다. 로드밸런서
    뒤에 놓이면 LB 의 주소가 남는데, 위조된 값보다는 그쪽이 낫다.
    """
    return request.client.host if request.client else None


async def _record_isolated(rows: list[dict]) -> None:
    """감사행을 요청 트랜잭션과 분리해 저장한다.

    기록이 실패해도 검사 결과(400/409)는 그대로 나가야 한다. 여기서 예외를 올리면
    "막았는데 기록이 안 됐다"가 "막지도 못했다"로 바뀐다.
    """
    if not rows:
        return
    try:
        async with AsyncSessionLocal() as audit_db:
            audit_db.add_all([PiiDetection(**row) for row in rows])
            await audit_db.commit()
    except Exception:  # noqa: BLE001 - 감사 기록 실패가 차단을 무효화하면 안 된다
        logger.exception("PII 탐지 기록에 실패했습니다. detections=%s", len(rows))


async def guard_text(
    text: str | None,
    *,
    source_table: str,
    source_column: str,
    source_endpoint: str,
    confirmed: bool = False,
    source_id: int | None = None,
    actor_employee_code: str | None = None,
    actor_ip: str | None = None,
) -> ScanResult:
    """텍스트 한 건을 검사하고, 필요하면 막고, 탐지가 있으면 기록한다.

    Args:
        text: 검사 대상. None/빈 문자열이면 검사 없이 통과한다.
        confirmed: 사용자가 FLAG 를 확인하고 재제출했는가(`confirm_pii`).
        source_id: 원본 행 id. BLOCK 은 저장 자체가 없으므로 항상 None 이다.

    Raises:
        PiiDetectedException: BLOCK 이면 400, FLAG 이고 미확인이면 409.
    """
    result = scan(text)
    if not result.detections:
        return result

    if result.is_blocked:
        action = ACTION_REJECTED
    elif confirmed:
        action = ACTION_PROCEEDED_CONFIRMED
    else:
        action = ACTION_RECORDED

    await _record_isolated(
        [
            {
                **d.to_audit_row(),
                "source_table": source_table,
                "source_column": source_column,
                # BLOCK 은 원본 행이 없다. 위 docstring 참조.
                "source_id": None if result.is_blocked else source_id,
                "source_endpoint": source_endpoint,
                "action_taken": action,
                "actor_employee_code": actor_employee_code,
                "actor_ip": actor_ip,
            }
            for d in result.detections
        ]
    )

    if result.is_blocked:
        raise PiiDetectedException(
            status.HTTP_400_BAD_REQUEST, "PII_DETECTED", BLOCK_MESSAGE, result
        )
    if result.needs_confirmation and not confirmed:
        raise PiiDetectedException(
            status.HTTP_409_CONFLICT, "PII_CONFIRMATION_REQUIRED", FLAG_MESSAGE, result
        )
    return result
