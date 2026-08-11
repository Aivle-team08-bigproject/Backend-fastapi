"""DLP 탐지 결과 타입.

설계 근거는 `회신md/DLP_설계_협의.md`. 요약하면 세 가지다.

  1. 탐지된 *값* 은 어디에도 담지 않는다. 담는 순간 이 구조체와 DB 가 새로운
     유출 지점이 된다. `detector_code + match_count` 로 "무엇을 몇 건 막았는가"는
     충분히 증명된다.
  2. `offsets` 는 API 응답 전용이다. 프론트가 입력창에서 하이라이트하는 데만 쓰고
     `service.pii_detections` 에는 저장하지 않는다. 응답은 휘발되지만 DB 는 영구다.
  3. `checksum_valid` 는 3값이다. True/False 와 "체크섬이 없는 탐지기(None)"를
     구분해야 감사 기록이 의미를 갖는다.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field


class Severity(str, enum.Enum):
    """탐지 심각도. 문자열 값이 그대로 `pii_detections.severity` 에 들어간다."""

    BLOCK = "BLOCK"
    FLAG = "FLAG"

    @property
    def rank(self) -> int:
        """전체 처분을 정할 때 쓰는 서열. 큰 쪽이 이긴다."""
        return 2 if self is Severity.BLOCK else 1


class Disposition(str, enum.Enum):
    """텍스트 한 건에 대한 최종 처분.

    BLOCK  400.  저장하지 않고 LLM 전송도 하지 않는다
    FLAG   409.  되묻는다 → 사용자가 고치거나 confirm 후 재제출
    PASS   통과
    """

    BLOCK = "BLOCK"
    FLAG = "FLAG"
    PASS = "PASS"


@dataclass(frozen=True)
class Match:
    """탐지기가 찾은 개별 구간. 내부 계산용이라 외부로 나가지 않는다."""

    start: int
    end: int
    checksum_valid: bool | None
    severity: Severity


@dataclass(frozen=True)
class Detection:
    """탐지기 하나의 집계 결과. API 응답과 DB 기록의 원본이다."""

    detector_code: str
    severity: Severity
    match_count: int
    checksum_valid: bool | None
    offsets: list[tuple[int, int]] = field(default_factory=list)

    def to_response(self) -> dict:
        """API 응답용. offsets 를 포함한다."""
        return {
            "detector_code": self.detector_code,
            "severity": self.severity.value,
            "match_count": self.match_count,
            "offsets": [[s, e] for s, e in self.offsets],
        }

    def to_audit_row(self) -> dict:
        """`service.pii_detections` INSERT 용. offsets 를 뺀다.

        나머지 컬럼(source_table·source_id·actor_ip 등)은 호출하는 서비스 계층이
        채운다. 탐지기는 자기가 아는 것만 넘긴다.
        """
        return {
            "detector_code": self.detector_code,
            "severity": self.severity.value,
            "match_count": self.match_count,
            "checksum_valid": self.checksum_valid,
        }


@dataclass(frozen=True)
class ScanResult:
    """텍스트 한 건의 검사 결과."""

    disposition: Disposition
    detections: list[Detection]

    @property
    def is_blocked(self) -> bool:
        return self.disposition is Disposition.BLOCK

    @property
    def needs_confirmation(self) -> bool:
        return self.disposition is Disposition.FLAG

    def to_response(self) -> dict:
        """400/409 응답 본문.

        error_code 는 호출부에서 붙인다 — BLOCK 이면 PII_DETECTED,
        FLAG 면 PII_CONFIRMATION_REQUIRED.
        """
        return {"detections": [d.to_response() for d in self.detections]}


PASSED = ScanResult(disposition=Disposition.PASS, detections=[])
