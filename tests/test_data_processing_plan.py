import json

import pytest

from agent_runtime.data_processing.agent import run
from agent_runtime.data_processing.plan import (
    ProcessingPlan,
    ProcessingPlanError,
    validate_processing_plan,
)
from agent_runtime.data_processing import planning_agent


def _selection() -> dict:
    return {
        "source_columns": [
            {"column": "approved_at"},
            {"column": "amount"},
            {"column": "customer_id"},
        ],
        "selection_query": {
            "columns": ["approved_at", "amount", "customer_id"],
            "filters": {},
        },
    }


def _plan() -> dict:
    return {
        "plan_version": "1.0",
        "objective": "월별 결제금액 집계",
        "operations": [
            {
                "id": "op-1",
                "type": "derive_date_part",
                "source_columns": ["approved_at"],
                "target_column": "transaction_month",
                "parameters": {"part": "month"},
                "reason": "월별 추이를 만들기 위해 월을 파생",
            },
            {
                "id": "op-2",
                "type": "cast",
                "source_columns": ["amount"],
                "target_column": None,
                "parameters": {"data_type": "number"},
                "reason": "금액 합계를 위해 숫자로 변환",
            },
            {
                "id": "op-3",
                "type": "aggregate",
                "source_columns": ["transaction_month", "amount"],
                "target_column": None,
                "parameters": {
                    "group_by": ["transaction_month"],
                    "metrics": [
                        {"column": "amount", "function": "sum", "target": "monthly_amount"}
                    ],
                },
                "reason": "월별 결제금액 합계 계산",
            },
            {
                "id": "op-4",
                "type": "sort",
                "source_columns": ["transaction_month"],
                "target_column": None,
                "parameters": {"direction": "asc"},
                "reason": "시간순 정렬",
            },
        ],
        "output": {
            "columns": ["transaction_month", "monthly_amount"],
            "formats": ["api", "csv", "report"],
        },
        "quality_checks": [
            {"type": "not_null", "column": "transaction_month"},
            {"type": "non_negative", "column": "monthly_amount"},
        ],
        "explanation": "승인된 거래를 월별로 합산합니다.",
    }


def _planning_step_result(prompt: str) -> dict:
    plan = _plan()
    if prompt == planning_agent.DEDUPLICATION_PLAN_PROMPT:
        return {"operations": []}
    if prompt == planning_agent.MISSING_VALUE_PLAN_PROMPT:
        return {"operations": [plan["operations"][1]]}
    if prompt == planning_agent.DERIVED_COLUMN_ORDER_PROMPT:
        return {"operations": [
            plan["operations"][0],
            plan["operations"][2],
            plan["operations"][3],
        ]}
    return {
        "plan_version": plan["plan_version"],
        "objective": plan["objective"],
        "operations": [
            {
                "id": "final-1",
                "type": "select_columns",
                "source_columns": [],
                "target_column": None,
                "parameters": {"columns": plan["output"]["columns"]},
                "reason": "최종 제공 컬럼을 확정",
            }
        ],
        "output": plan["output"],
        "quality_checks": plan["quality_checks"],
        "explanation": plan["explanation"],
    }


def test_processing_plan_drives_operation_order():
    result = run(
        {
            "raw_requirement": "월별 결제 추이",
            "analysis": {"delivery_channel": "api"},
            "selection": _selection(),
            "processing_plan": _plan(),
            "selected_rows": [
                {"approved_at": "2026-01-02T10:00:00", "amount": "1000", "customer_id": "a"},
                {"approved_at": "2026-01-20T10:00:00", "amount": "2500", "customer_id": "b"},
                {"approved_at": "2026-02-03T10:00:00", "amount": "500", "customer_id": "c"},
            ],
        }
    )

    assert result["ok"] is True
    data = result["data"]
    assert data["processing_engine"] == "llm-plan-deterministic-executor-v1"
    assert [item["type"] for item in data["processing_explanation"]["operation_execution"]] == [
        "derive_date_part",
        "cast",
        "aggregate",
        "sort",
    ]
    assert data["api_result"]["items"] == [
        {"transaction_month": "2026-01", "monthly_amount": 3500.0},
        {"transaction_month": "2026-02", "monthly_amount": 500.0},
    ]
    assert data["processing_plan_sha256"]


def test_processing_plan_rejects_unapproved_column():
    raw = _plan()
    raw["operations"][0]["source_columns"] = ["card_number"]
    plan = ProcessingPlan.model_validate(raw)

    with pytest.raises(ProcessingPlanError, match="unavailable columns"):
        validate_processing_plan(plan, _selection())


def test_processing_plan_rejects_arbitrary_operation_parameter():
    raw = _plan()
    raw["operations"][0]["parameters"]["python_code"] = "do_not_execute()"
    plan = ProcessingPlan.model_validate(raw)

    with pytest.raises(ProcessingPlanError, match="unsupported parameters"):
        validate_processing_plan(plan, _selection())


def test_planning_agent_never_sends_selected_rows(monkeypatch):
    captured: list[dict] = []

    class FakeAgent:
        def __init__(self, result):
            self.result = result

        def __call__(self, prompt: str) -> str:
            captured.append(json.loads(prompt))
            return json.dumps(self.result, ensure_ascii=False)

    monkeypatch.setattr(
        planning_agent,
        "build_agent",
        lambda prompt: FakeAgent(_planning_step_result(prompt)),
    )
    payload = {
        "raw_requirement": "월별 결제 추이",
        "analysis": {"output_formats": ["csv"]},
        "selection": _selection(),
        "selected_rows": [{"customer_id": "must-not-leak"}],
    }

    assert planning_agent.create_processing_plan(payload)["objective"] == "월별 결제금액 집계"
    assert len(captured) == 4
    assert all("selected_rows" not in request for request in captured)


def test_planning_agent_sends_final_hitl_feedback_without_rows(monkeypatch):
    captured: list[dict] = []

    class FakeAgent:
        def __init__(self, result):
            self.result = result

        def __call__(self, prompt: str) -> str:
            captured.append(json.loads(prompt))
            return json.dumps(self.result, ensure_ascii=False)

    monkeypatch.setattr(
        planning_agent,
        "build_agent",
        lambda prompt: FakeAgent(_planning_step_result(prompt)),
    )

    planning_agent.create_processing_plan(
        {
            "raw_requirement": "월별 결제 추이",
            "analysis": {},
            "selection": _selection(),
            "hitl_feedback": "월별 합계가 아니라 평균 금액으로 다시 만들어 주세요.",
            "selected_rows": [{"customer_id": "must-not-leak"}],
        }
    )

    assert len(captured) == 4
    assert all(
        request["hitl_feedback"] == "월별 합계가 아니라 평균 금액으로 다시 만들어 주세요."
        for request in captured
    )
    assert all("selected_rows" not in request for request in captured)


def test_planning_agent_reports_four_ordered_steps(monkeypatch):
    class FakeAgent:
        def __init__(self, result):
            self.result = result

        def __call__(self, prompt: str) -> str:
            return json.dumps(self.result, ensure_ascii=False)

    monkeypatch.setattr(
        planning_agent,
        "build_agent",
        lambda prompt: FakeAgent(_planning_step_result(prompt)),
    )
    events = []

    plan = planning_agent.create_processing_plan(
        {"raw_requirement": "월별 결제 추이", "selection": _selection()},
        on_step=lambda code, status, metadata: events.append((code, status, metadata)),
    )

    assert plan["operations"][-1]["type"] == "select_columns"
    assert [(code, status) for code, status, _ in events] == [
        ("DEDUPLICATION_PLAN", "RUNNING"),
        ("DEDUPLICATION_PLAN", "COMPLETED"),
        ("MISSING_VALUE_PLAN", "RUNNING"),
        ("MISSING_VALUE_PLAN", "COMPLETED"),
        ("DERIVED_COLUMN_ORDER", "RUNNING"),
        ("DERIVED_COLUMN_ORDER", "COMPLETED"),
        ("FINAL_COLUMN_VALIDATION", "RUNNING"),
        ("FINAL_COLUMN_VALIDATION", "COMPLETED"),
    ]


def test_planning_agent_normalizes_unambiguous_cast_parameter_alias():
    result = {"operations": [{
        "id": "missing-1",
        "type": "cast",
        "source_columns": ["amount"],
        "target_column": None,
        "parameters": {"type": "number"},
        "reason": "금액 계산",
    }]}

    operations = planning_agent._validate_operations_result(
        result,
        allowed_types={"cast"},
        previous_operations=[],
        selection=_selection(),
    )

    assert operations[0].parameters == {"data_type": "number"}


def test_planning_agent_normalizes_unambiguous_aggregate_parameter_alias():
    previous = [ProcessingPlan.model_validate(_plan()).operations[0]]
    result = {"operations": [{
        "id": "derived-2",
        "type": "aggregate",
        "source_columns": ["transaction_month", "amount"],
        "target_column": None,
        "parameters": {
            "group_by": ["transaction_month"],
            "aggregation": {
                "column": "amount",
                "function": "sum",
                "target": "monthly_amount",
            },
        },
        "reason": "월별 집계",
    }]}

    operations = planning_agent._validate_operations_result(
        result,
        allowed_types={"aggregate"},
        previous_operations=previous,
        selection=_selection(),
    )

    assert operations[0].parameters["metrics"][0]["target"] == "monthly_amount"
