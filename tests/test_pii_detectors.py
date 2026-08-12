"""DLP 탐지기 — 검증 벡터 통과 시험.

`tests/pii_test_vectors.py` 가 명세의 실행 가능한 형태다. **벡터가 실패하면
명세를 고친 게 아니라 구현이 틀린 것이다.**

판정 기준 (벡터 파일 하단):

    재현율   RRN/BRN/CARD/PHONE/EMAIL/ACCOUNT/PASSPORT 를 100% 잡아야 한다
    오탐률   FALSE_POSITIVE_VECTORS 는 0건이어야 한다
    전체 처분 BLOCK 과 FLAG 가 섞이면 BLOCK 이 이긴다
"""

from __future__ import annotations

import pytest

from app.common.pii import Disposition, scan
from tests.pii_test_vectors import ALL_VECTORS, FALSE_POSITIVE_VECTORS


def _by_code(result) -> dict:
    return {d.detector_code: d for d in result.detections}


@pytest.mark.parametrize("vector", ALL_VECTORS, ids=lambda v: v["id"])
def test_vector(vector: dict) -> None:
    result = scan(vector["text"])
    found = _by_code(result)
    expected = {e["detector"]: e for e in vector["expect"]}

    # 놓친 것
    for code, spec in expected.items():
        assert code in found, f"{vector['id']}: {code} 를 놓쳤다 — {vector['why']}"
        detection = found[code]
        assert detection.severity.value == spec["severity"], (
            f"{vector['id']}: {code} 심각도가 {detection.severity.value}, "
            f"기대는 {spec['severity']} — {vector['why']}"
        )
        if "count" in spec:
            assert detection.match_count == spec["count"], (
                f"{vector['id']}: {code} 건수가 {detection.match_count}, 기대는 {spec['count']}"
            )
        if "checksum_valid" in spec:
            assert detection.checksum_valid == spec["checksum_valid"], (
                f"{vector['id']}: {code} checksum_valid 가 {detection.checksum_valid}, "
                f"기대는 {spec['checksum_valid']}"
            )

    # 잘못 잡은 것 — 오탐이 재현율만큼 중요하다
    unexpected = set(found) - set(expected)
    assert not unexpected, f"{vector['id']}: 오탐 {sorted(unexpected)} — {vector['why']}"

    # 전체 처분
    if not expected:
        assert result.disposition is Disposition.PASS
    elif any(e["severity"] == "BLOCK" for e in expected.values()):
        assert result.disposition is Disposition.BLOCK, (
            f"{vector['id']}: BLOCK 이 하나라도 있으면 전체 처분은 BLOCK 이어야 한다"
        )
    else:
        assert result.disposition is Disposition.FLAG


@pytest.mark.parametrize("vector", FALSE_POSITIVE_VECTORS, ids=lambda v: v["id"])
def test_no_false_positive(vector: dict) -> None:
    """오탐 벡터는 전부 우리 도메인의 실제 요구사항 문장에서 뽑은 것이다.

    여기서 한 건이라도 걸리면 실사용에서는 훨씬 자주 걸린다.
    """
    result = scan(vector["text"])
    assert result.disposition is Disposition.PASS, (
        f"{vector['id']}: {[d.detector_code for d in result.detections]} 오탐 — {vector['why']}"
    )


def test_none_and_empty() -> None:
    assert scan(None).disposition is Disposition.PASS
    assert scan("").disposition is Disposition.PASS


def test_audit_row_has_no_offsets() -> None:
    """DB 기록에는 위치가 들어가면 안 된다.

    응답은 휘발되지만 DB 는 영구다. 위치를 남기면 이 테이블이 새로운 유출 지점이 된다.
    """
    result = scan("주민 880101-1234568 입니다.")
    row = result.detections[0].to_audit_row()
    assert "offsets" not in row
    assert set(row) == {"detector_code", "severity", "match_count", "checksum_valid"}


def test_response_has_offsets() -> None:
    """응답에는 위치가 있어야 프론트가 하이라이트할 수 있다."""
    result = scan("주민 880101-1234568 입니다.")
    body = result.to_response()
    assert body["detections"][0]["offsets"] == [[3, 17]]
