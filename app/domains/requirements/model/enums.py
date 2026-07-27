"""service.requirements 신규 상태값 체계 (V7 CHECK 제약과 1:1 일치).

기존 app/models/enums.py의 RequirementStatus(DRAFT/REVIEW/APPROVED/...)와는
완전히 다른 값 체계다 - 이름이 같은 클래스를 재사용하지 않고 별도로 둔다.
"""

from enum import Enum


class RequirementStatus(str, Enum):
    RECEIVED = "RECEIVED"
    ANALYZING = "ANALYZING"
    IN_REVIEW = "IN_REVIEW"
    PROCESSING = "PROCESSING"
    QA = "QA"
    SAMPLE_SENT = "SAMPLE_SENT"
    FEEDBACK_PENDING = "FEEDBACK_PENDING"
    CONFIRMED = "CONFIRMED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"


class ProvideFormat(str, Enum):
    CSV = "CSV"
    EXCEL = "EXCEL"
    API = "API"