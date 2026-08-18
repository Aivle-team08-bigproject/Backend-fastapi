"""DLP 검사 지점 결선 시험.

탐지기 자체는 `tests/test_pii_detectors.py`(46개)가 검증한다. 여기서는 **결선**만 본다.

  1. BLOCK 이 400 으로 나가고 detections 가 본문에 실리는가
  2. FLAG 는 409 로 되묻고, confirm 하면 통과하는가
  3. 감사행이 요청 트랜잭션과 분리돼 남는가 — 예외를 던져도 살아남아야 한다
  4. action_taken 이 실제로 일어난 일을 기록하는가

DB 는 붙이지 않는다. `_record_isolated` 를 가로채 인자만 본다. 이 시험의 관심은
"어떤 행을 남기려 했는가"이고, 그 행이 실제로 들어가는지는 스키마와 권한의 문제다.
"""

import pytest

from app.common.pii import guard
from app.common.pii.guard import (
    ACTION_PROCEEDED_CONFIRMED,
    ACTION_RECORDED,
    ACTION_REJECTED,
    PiiDetectedException,
    guard_text,
)

# 체크섬이 유효한 주민등록번호. 마지막 자리를 틀리면 BLOCK 이 아니라 FLAG 가 된다
# (2020-10 이후 발급분은 체크섬이 성립하지 않아 PASS 로 두지 않는다).
RRN_BLOCK = "기준 고객은 주민등록번호 900101-1234568 입니다."
PHONE_FLAG = "문의는 010-1234-5678 로 주세요."
CLEAN = "2025년 하반기 서울 지역 30대 여성의 업종별 소비 패턴을 분석해 주세요."


@pytest.fixture
def recorded(monkeypatch):
    """`_record_isolated` 를 가로채 남기려 한 행을 모은다."""
    rows: list[dict] = []

    async def fake(batch):
        rows.extend(batch)

    monkeypatch.setattr(guard, "_record_isolated", fake)
    return rows


def _call(text, **kwargs):
    import asyncio

    return asyncio.run(
        guard_text(
            text,
            source_table="data_requests",
            source_column="raw_requirement",
            source_endpoint="POST /api/v1/data-requests",
            **kwargs,
        )
    )


def test_clean_text_passes_without_recording(recorded):
    result = _call(CLEAN)
    assert result.detections == []
    assert recorded == []


def test_none_text_passes(recorded):
    assert _call(None).detections == []
    assert recorded == []


def test_block_raises_400_with_detections(recorded):
    with pytest.raises(PiiDetectedException) as exc:
        _call(RRN_BLOCK)

    assert exc.value.status_code == 400
    assert exc.value.detail["code"] == "PII_DETECTED"
    # 프론트가 입력창을 하이라이트할 수 있어야 한다
    codes = [d["detector_code"] for d in exc.value.detail["detections"]]
    assert "RRN" in codes
    assert exc.value.detail["detections"][0]["offsets"]


def test_block_records_before_raising(recorded):
    """예외를 던지기 *전에* 기록해야 한다. 막고도 흔적이 없으면 감사 요건을 못 지킨다."""
    with pytest.raises(PiiDetectedException):
        _call(RRN_BLOCK)

    assert len(recorded) == 1
    assert recorded[0]["action_taken"] == ACTION_REJECTED
    # BLOCK 은 원본 행이 저장되지 않으므로 참조할 id 가 없다
    assert recorded[0]["source_id"] is None


def test_block_ignores_confirmation(recorded):
    """confirm 으로 BLOCK 을 통과시킬 수 없어야 한다."""
    with pytest.raises(PiiDetectedException) as exc:
        _call(RRN_BLOCK, confirmed=True)

    assert exc.value.status_code == 400
    assert recorded[0]["action_taken"] == ACTION_REJECTED


def test_flag_raises_409_and_records(recorded):
    with pytest.raises(PiiDetectedException) as exc:
        _call(PHONE_FLAG)

    assert exc.value.status_code == 409
    assert exc.value.detail["code"] == "PII_CONFIRMATION_REQUIRED"
    assert recorded[0]["action_taken"] == ACTION_RECORDED


def test_flag_passes_when_confirmed(recorded):
    """경고를 보고도 넣은 경우가 감사에서 가장 중요하다."""
    result = _call(PHONE_FLAG, confirmed=True, source_id=7)

    assert result.detections
    assert recorded[0]["action_taken"] == ACTION_PROCEEDED_CONFIRMED
    assert recorded[0]["source_id"] == 7


def test_audit_row_never_carries_the_matched_value(recorded):
    """탐지된 값도, 위치도 DB 에 남기지 않는다. 남기면 이 테이블이 새 유출 지점이 된다."""
    with pytest.raises(PiiDetectedException):
        _call(RRN_BLOCK)

    row = recorded[0]
    assert "offsets" not in row
    assert not any("900101" in str(v) for v in row.values())
    assert set(row) == {
        "detector_code",
        "severity",
        "match_count",
        "checksum_valid",
        "source_table",
        "source_column",
        "source_id",
        "source_endpoint",
        "action_taken",
        "actor_employee_code",
        "actor_ip",
    }


def test_block_still_raises_when_audit_db_is_down(monkeypatch):
    """기록이 깨져도 차단은 그대로 나가야 한다.

    여기서만 `_record_isolated` 를 가로채지 않는다. 감사 세션을 실제로 망가뜨려
    "기록 실패 → 차단 무효화"라는 최악의 경로가 열려 있지 않은지 확인한다.
    """

    class BrokenSession:
        async def __aenter__(self):
            raise RuntimeError("connection refused")

        async def __aexit__(self, *a):
            return False

    monkeypatch.setattr(guard, "AsyncSessionLocal", lambda: BrokenSession())

    with pytest.raises(PiiDetectedException) as exc:
        _call(RRN_BLOCK)

    assert exc.value.status_code == 400
