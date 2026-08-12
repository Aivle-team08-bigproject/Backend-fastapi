"""`service.pii_detections` — DLP 탐지 이력.

DDL 은 `alembic/versions/d5f2a86c0b14_add_pii_detection_ledger.py` 가 정본이다.
이 파일은 그것을 SQLAlchemy 로 옮긴 것이고, **컬럼·인덱스 이름·COMMENT 가 DDL 과
정확히 일치해야 한다** — 어긋나면 `alembic check` 가 매번 차이를 보고한다.

## 🔴 append-only 다

`app_svc` 에게 `SELECT, INSERT` 만 부여돼 있다(`REVOKE UPDATE, DELETE`).
감사 이력을 애플리케이션 권한으로 고치거나 지울 수 없어야 하기 때문이다.

    db.add(PiiDetection(...))          ✅
    detection.action_taken = "..."     ❌ 런타임에 permission denied
    db.delete(detection)               ❌ 같음

보존기간에 따른 파기는 `hanacard_admin` 권한의 별도 배치가 수행한다.

## 탐지된 값은 저장하지 않는다

`detector_code + match_count` 로 "무엇을 언제 어디서 몇 건 막았는가"는 충분히
증명된다. 값이나 위치(offsets)를 남기면 이 테이블 자체가 새로운 유출 지점이 되고
접근통제를 또 설계해야 한다. 응답에는 offsets 를 주지만 DB 에는 넣지 않는다 —
응답은 휘발되고 DB 는 영구라 기준이 다르다.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Index,
    Integer,
    PrimaryKeyConstraint,
    String,
    text,
)
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base


class PiiDetection(Base):
    """탐지 한 건. `ScanResult.detections` 의 원소마다 한 행씩 남긴다.

    탐지기가 주는 것은 `Detection.to_audit_row()` 의 4개(detector_code · severity ·
    match_count · checksum_valid)뿐이고, 나머지는 호출하는 서비스 계층이 채운다.
    """

    __tablename__ = "pii_detections"
    __table_args__ = (
        PrimaryKeyConstraint("id", name="pk_pii_detections"),
        Index("ix_pii_detections_detected_at", "detected_at"),
        # 탐지기별 집계 — 오탐률을 보고 정규식을 조정하는 근거가 된다.
        Index("ix_pii_detections_detector_code", "detector_code"),
        # 특정 요청 한 건의 탐지 이력 조회.
        Index("ix_pii_detections_source", "source_table", "source_id"),
        {
            "schema": "service",
            "comment": "DLP 탐지 이력. 탐지된 값 자체는 저장하지 않는다(패턴 코드와 건수만).",
        },
    )

    id: Mapped[int] = mapped_column(BigInteger, autoincrement=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=text("now()"), nullable=False
    )

    # --- 어디서 걸렸나 ---
    source_table: Mapped[str] = mapped_column(String(64), nullable=False)
    source_column: Mapped[str] = mapped_column(String(64), nullable=False)
    # BLOCK 은 원본 행이 저장되지 않으므로 참조할 id 가 없다. FK 를 걸지 않는 것도 같은 이유.
    source_id: Mapped[int | None] = mapped_column(BigInteger)
    source_endpoint: Mapped[str] = mapped_column(String(200), nullable=False)

    # --- 무엇이 걸렸나 ---
    detector_code: Mapped[str] = mapped_column(String(20), nullable=False)
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    # 체크섬이 없는 탐지기(PHONE/EMAIL 등)는 NULL. false 와 구분해야 한다 —
    # false 는 "체크섬을 돌렸는데 틀렸다"이고 NULL 은 "돌릴 체크섬이 없다"이다.
    checksum_valid: Mapped[bool | None] = mapped_column(Boolean)
    match_count: Mapped[int] = mapped_column(Integer, nullable=False)

    # --- 실제로 무슨 일이 일어났나 ---
    # severity 는 탐지기의 판정이고 action_taken 은 결과다.
    # REJECTED / PROCEEDED_CONFIRMED / RECORDED
    # FLAG 를 사용자가 confirm 하고 진행한 경우가 감사에서 가장 중요하다.
    action_taken: Mapped[str] = mapped_column(String(24), nullable=False)

    # --- 누가 ---
    # POST /api/v1/data-requests 에 아직 인증이 없어 nullable 이다.
    # admin_audit_logs.actor_employee_code 가 NOT NULL 인 것과 다른 이유.
    actor_employee_code: Mapped[str | None] = mapped_column(String(40))
    actor_ip: Mapped[str | None] = mapped_column(INET)
