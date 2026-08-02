import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.common.errors import DomainException
from app.domains.pipeline.service import get_sample_preview


def _selection_output() -> dict:
    return {
        "selected_tables": [{"table": "merchant", "reason": "지역 분석"}],
        "source_columns": [{"dataset": "merchant", "column": "merchant_region"}],
        "derived_columns": [{"name": "결제건수", "source_columns": ["transaction_id"]}],
        "selection_query": {
            "columns": ["merchant_region", "transaction_id"],
            "filters": {
                "merchant_region": {
                    "operator": "eq",
                    "value": "서울",
                    "reason": "서울 지역 요청",
                    "evidence": "가맹점 지역 COMMENT",
                }
            },
        },
        "interpretations": [
            {
                "term": "서울 지역",
                "interpreted_as": "merchant_region=서울",
                "reason": "가맹점 지역 COMMENT",
                "requires_confirmation": True,
            }
        ],
        "catalog_issues": [],
        "catalog_matches": [
            {
                "term": "서울 지역",
                "catalog": "region_catalog",
                "matches": [{"code": "11", "label": "서울"}],
                "reason": "지역 카탈로그 매칭",
                "requires_confirmation": True,
            }
        ],
        "sample_columns": [
            {
                "name": "merchant_region",
                "data_type": "string",
                "is_derived": False,
                "source_columns": ["merchant_region"],
                "description": "가맹점 지역",
            },
            {
                "name": "결제건수",
                "data_type": "integer",
                "is_derived": True,
                "source_columns": ["transaction_id"],
                "description": "지역별 결제 건수",
            },
        ],
        "sample_rows": [
            {"merchant_region": "서울", "결제건수": index}
            for index in range(1, 6)
        ],
        "sample_metadata": {
            "is_synthetic": True,
            "sample_count": 5,
            "notice": "실제 고객 데이터가 아닌 형식 확인용 예시 데이터입니다.",
        },
    }


def test_get_sample_preview_returns_saved_selection_sample():
    db = AsyncMock()
    db.get.return_value = SimpleNamespace(id=17)
    db.scalar.return_value = SimpleNamespace(
        attempt_no=2,
        output_payload=_selection_output(),
    )

    response = asyncio.run(get_sample_preview(db, 17))

    assert response.run_id == 17
    assert response.stage == "DATA_SELECTION"
    assert response.attempt_no == 2
    assert len(response.columns) == 2
    assert response.columns[1].is_derived is True
    assert response.rows == [
        {"merchant_region": "서울", "결제건수": index}
        for index in range(1, 6)
    ]
    assert response.metadata.is_synthetic is True
    assert response.metadata.sample_count == 5
    assert response.selection_query["filters"]["merchant_region"]["value"] == "서울"
    assert response.interpretations[0]["requires_confirmation"] is True
    assert response.catalog_issues == []
    assert response.catalog_matches[0]["matches"][0]["label"] == "서울"
    assert response.review_summary.requires_confirmation is True
    assert response.review_summary.confirmation_terms == ["서울 지역"]
    assert response.review_summary.has_catalog_issues is False
    assert response.review_summary.catalog_issue_count == 0


def test_get_sample_preview_reports_not_ready_without_completed_selection():
    db = AsyncMock()
    db.get.return_value = SimpleNamespace(id=17)
    db.scalar.return_value = None

    with pytest.raises(DomainException) as error:
        asyncio.run(get_sample_preview(db, 17))

    assert error.value.status_code == 404
    assert error.value.code == "PIPELINE_SAMPLE_NOT_READY"


def test_get_sample_preview_rejects_invalid_saved_payload():
    db = AsyncMock()
    db.get.return_value = SimpleNamespace(id=17)
    db.scalar.return_value = SimpleNamespace(
        attempt_no=1,
        output_payload={"sample_columns": [], "sample_rows": []},
    )

    with pytest.raises(DomainException) as error:
        asyncio.run(get_sample_preview(db, 17))

    assert error.value.status_code == 409
    assert error.value.code == "PIPELINE_SAMPLE_INVALID"
