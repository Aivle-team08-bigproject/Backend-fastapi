"""승인된 데이터 선별 계획의 정규화와 무결성 검증."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy


APPROVED_PLAN_FIELDS = (
    "selected_tables",
    "source_columns",
    "derived_columns",
    "selection_query",
    "interpretations",
    "catalog_matches",
    "catalog_issues",
)


def snapshot_selection_plan(output: dict) -> dict:
    """실제 가공과 설명에 필요한 선별 결과만 승인 스냅샷으로 복제한다."""
    return {
        field: deepcopy(output.get(field, [] if field != "selection_query" else {}))
        for field in APPROVED_PLAN_FIELDS
    }


def selection_plan_sha256(plan: dict) -> str:
    canonical = json.dumps(
        plan,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def verify_selection_plan(plan: dict, expected_sha256: str) -> None:
    if selection_plan_sha256(plan) != expected_sha256:
        raise ValueError("approved selection plan hash mismatch")
