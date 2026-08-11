"""DLP — 민감정보 탐지.

호출부는 이것만 알면 된다.

    from app.common.pii import scan

    result = scan(raw_requirement)
    if result.is_blocked:
        # 400 PII_DETECTED.  저장하지 않고 LLM 전송도 하지 않는다
    elif result.needs_confirmation and not payload.confirm_pii:
        # 409 PII_CONFIRMATION_REQUIRED.  사용자가 고치거나 confirm 후 재제출
    # 어느 쪽이든 result.detections 를 service.pii_detections 에 남긴다

검사 지점은 세 곳이다(`회신md/DLP_설계_협의.md` 7절).

    pipeline/service.py    create_data_request 진입부   raw_requirement
    pipeline               submit_stage_review          reviews.feedback
    employees/service.py   직원 반려                     rejected_reason

**Pydantic validator 가 아니라 서비스 계층에 둔다.** validator 는 스키마마다
반복되고, 탐지 이력을 남길 DB 세션이 없다.

## 왜 제출 시점에 동기적으로 끝나야 하는가

HITL 검토 게이트에서 판단하면 **이미 LLM 으로 나간 뒤**다. 첫 게이트가
`REQUIREMENT_ANALYSIS` 결과를 보는 것이기 때문이다.

    제출 → (검사)          ← 여기서 막으면 LLM 전송도 agent 실행도 없다
       → 파이프라인 QUEUED
       → agent_client 가 LLM 호출   ← 이미 나감. 되돌릴 수 없다
       → HITL 게이트                ← 여기서 판단하면 늦다

## 마스킹(자동 치환)을 하지 않는 이유

  1. 마스킹 로직에 버그가 있으면 조용히 샌다. BLOCK 은 실패해도 막히는 쪽으로 실패한다
  2. 사용자가 자기가 뭘 잘못 썼는지 모른다. 400 으로 알려주는 편이 낫다
  3. 원본이 필요한 경우가 있다 (요구사항 재검토·분쟁)
"""

from __future__ import annotations

from app.common.pii.detectors import DETECTOR_PRIORITY
from app.common.pii.types import (
    PASSED,
    Detection,
    Disposition,
    Match,
    ScanResult,
    Severity,
)

__all__ = [
    "scan",
    "ScanResult",
    "Detection",
    "Disposition",
    "Severity",
    "PASSED",
]


def _overlaps(a: Match, b: Match) -> bool:
    return a.start < b.end and b.start < a.end


def scan(text: str | None) -> ScanResult:
    """텍스트 한 건을 검사한다.

    같은 구간을 두 탐지기가 잡으면 `DETECTOR_PRIORITY` 앞쪽만 남긴다.
    한 텍스트에 BLOCK 과 FLAG 가 섞이면 전체 처분은 BLOCK 이다.
    """
    if not text:
        return PASSED

    accepted: list[tuple[str, Match]] = []
    for code, detect in DETECTOR_PRIORITY:
        for match in detect(text):  # type: ignore[operator]
            if any(_overlaps(match, kept) for _, kept in accepted):
                continue
            accepted.append((code, match))

    if not accepted:
        return PASSED

    detections = _aggregate(accepted)
    worst = max(d.severity.rank for d in detections)
    disposition = Disposition.BLOCK if worst == Severity.BLOCK.rank else Disposition.FLAG
    return ScanResult(disposition=disposition, detections=detections)


def _aggregate(accepted: list[tuple[str, Match]]) -> list[Detection]:
    """탐지기별로 묶는다.

    한 탐지기 안에서 심각도가 갈릴 수 있다 — 주민번호 두 개가 각각 체크섬 통과/실패인
    경우다. 그때는 **심각한 쪽으로 올린다.** `checksum_valid` 도 마찬가지로,
    하나라도 통과했으면 True 로 둔다(그 텍스트에 확실한 주민번호가 있다는 뜻이므로).

    결과는 심각도 → 탐지기 코드 순으로 정렬한다. 응답에서 BLOCK 이 위에 오는 편이
    화면에서 읽기 좋다.
    """
    grouped: dict[str, list[Match]] = {}
    for code, match in accepted:
        grouped.setdefault(code, []).append(match)

    detections: list[Detection] = []
    for code, matches in grouped.items():
        matches.sort(key=lambda m: m.start)
        severity = Severity.BLOCK if any(m.severity is Severity.BLOCK for m in matches) else Severity.FLAG
        checksums = [m.checksum_valid for m in matches]
        if all(c is None for c in checksums):
            checksum_valid: bool | None = None
        else:
            checksum_valid = any(c is True for c in checksums)
        detections.append(
            Detection(
                detector_code=code,
                severity=severity,
                match_count=len(matches),
                checksum_valid=checksum_valid,
                offsets=[(m.start, m.end) for m in matches],
            )
        )

    detections.sort(key=lambda d: (-d.severity.rank, d.detector_code))
    return detections
