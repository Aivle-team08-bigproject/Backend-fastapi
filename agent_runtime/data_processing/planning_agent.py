"""승인된 선별 계획으로 4단계 가공 계획을 설계하는 LLM Agent."""

from __future__ import annotations

import json
import re

from strands import Agent
from strands.models.openai import OpenAIModel

from agent_runtime.data_processing.config import settings
from agent_runtime.data_processing.plan import (
    ProcessingOperation,
    ProcessingPlan,
    validate_processing_operations,
    validate_processing_plan,
)


COMMON_RULES = """실제 데이터 행은 제공되지 않으며 보려고 시도해서도 안 된다.
승인된 선별 계획의 컬럼만 사용한다. 임의 Python 코드, SQL, shell 명령을 만들지 않는다.
직접 식별자 가명화와 k-익명성은 결정론적 실행기가 강제하므로 기준을 변경하지 않는다.
hitl_feedback이 있으면 승인 범위 안에서 최종 반려 의견을 우선 반영한다.
JSON 하나 외에는 출력하지 않는다."""

DEDUPLICATION_PLAN_PROMPT = f"""당신은 데이터 가공 Agent의 중복 제거 계획 단계다.
고객 요구와 승인된 선별 계획을 근거로 중복 판단 컬럼을 결정한다. 중복 제거가 불필요하면
operations를 빈 배열로 반환한다. 앞뒤 단계의 계획은 만들지 않는다.

반환 계약:
{{"operations":[{{"id":"dedup-1","type":"deduplicate","source_columns":["컬럼"],
"target_column":null,"parameters":{{}},"reason":"근거"}}]}}

{COMMON_RULES}"""

MISSING_VALUE_PLAN_PROMPT = f"""당신은 데이터 가공 Agent의 결측 처리 계획 단계다.
앞에서 검증된 deduplication_plan을 변경하지 않고, 승인 컬럼별 형 변환과 결측 처리만 설계한다.
허용 operation은 cast와 fill_missing이다. 결측 처리가 불필요하면 operations를 빈 배열로 반환한다.
fill_missing strategy는 median, mode, zero, drop_row, keep_null 중 하나다.
cast의 parameters는 반드시 {{"data_type":"string|integer|number|date|datetime"}} 형식이다.

반환 계약:
{{"operations":[{{"id":"missing-1","type":"fill_missing","source_columns":["컬럼"],
"target_column":null,"parameters":{{"strategy":"keep_null"}},"reason":"근거"}}]}}

{COMMON_RULES}"""

DERIVED_COLUMN_ORDER_PROMPT = f"""당신은 데이터 가공 Agent의 파생 컬럼 생성 순서 결정 단계다.
앞의 중복·결측 계획을 변경하지 않고, 승인된 파생 컬럼 정의와 고객 목적을 만족하도록 의존관계
순서대로 operation을 설계한다. 허용 operation은 derive_date_part, bucketize, aggregate, sort다.
파생이나 집계가 필요 없으면 operations를 빈 배열로 반환한다.
aggregate parameters는 반드시 {{"group_by":["컬럼"],"metrics":[{{"column":"컬럼",
"function":"sum|count|count_distinct|avg|min|max","target":"새컬럼"}}]}} 형식이다.

반환 계약:
{{"operations":[{{"id":"derived-1","type":"derive_date_part","source_columns":["컬럼"],
"target_column":"새컬럼","parameters":{{"part":"month"}},"reason":"근거"}}]}}

{COMMON_RULES}"""

FINAL_COLUMN_VALIDATION_PROMPT = f"""당신은 데이터 가공 Agent의 최종 컬럼·품질 검증 정의 단계다.
앞의 세 단계 operation을 변경하지 않는다. 최종 출력 컬럼과 형식, 품질 검증을 확정하고 마지막에
select_columns operation을 정확히 하나 만든다. 이 단계 operation은 select_columns만 허용한다.
quality_checks type은 not_null, non_negative, unique 중 하나다.

반환 계약:
{{
  "plan_version":"1.0",
  "objective":"가공 목적",
  "operations":[{{"id":"final-1","type":"select_columns","source_columns":[],
    "target_column":null,"parameters":{{"columns":["최종 컬럼"]}},"reason":"근거"}}],
  "output":{{"columns":["최종 컬럼"],"formats":["api|csv|visualization|report"]}},
  "quality_checks":[{{"type":"not_null|non_negative|unique","column":"최종 컬럼"}}],
  "explanation":"고객에게 보여줄 설명"
}}

{COMMON_RULES}"""

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
MAX_ATTEMPTS = 3


def _build_model() -> OpenAIModel:
    return OpenAIModel(
        client_args={
            "api_key": settings.deepseek_api_key,
            "base_url": settings.deepseek_base_url,
            "timeout": settings.data_processing_model_timeout_seconds,
            "max_retries": 0,
        },
        model_id=settings.data_processing_model_id,
        params={"temperature": 0},
    )


def build_agent(system_prompt: str) -> Agent:
    return Agent(model=_build_model(), tools=[], system_prompt=system_prompt, callback_handler=None)


def _extract_json(raw: str) -> dict:
    match = _JSON_BLOCK_RE.search(raw)
    if match is None:
        raise ValueError("모델 응답에서 JSON을 찾지 못함")
    value = json.loads(match.group(0))
    if not isinstance(value, dict):
        raise ValueError("가공 단계 결과는 JSON 객체여야 함")
    return value


def _validate_operations_result(
    result: dict,
    *,
    allowed_types: set[str],
    previous_operations: list[ProcessingOperation],
    selection: dict,
) -> list[ProcessingOperation]:
    if set(result) != {"operations"} or not isinstance(result["operations"], list):
        raise ValueError("현재 단계 결과는 operations 배열 하나만 포함해야 함")
    normalized_items = [_normalize_operation_item(item) for item in result["operations"]]
    result["operations"] = normalized_items
    operations = [ProcessingOperation.model_validate(item) for item in normalized_items]
    invalid = {operation.type for operation in operations} - allowed_types
    if invalid:
        raise ValueError(f"현재 단계에 허용되지 않은 operation: {', '.join(sorted(invalid))}")
    combined = [*previous_operations, *operations]
    identifiers = [operation.id for operation in combined]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("processing operation ids must be unique")
    validate_processing_operations(combined, selection)
    return operations


def _normalize_operation_item(item: dict) -> dict:
    """LLM의 의미가 명확한 cast 키 별칭만 엄격한 실행 계약으로 정규화한다."""
    if not isinstance(item, dict):
        raise ValueError("processing operation은 JSON 객체여야 함")
    normalized = dict(item)
    parameters = dict(normalized.get("parameters") or {})
    if normalized.get("type") == "cast" and "type" in parameters:
        if "data_type" in parameters:
            raise ValueError("cast parameters에 type과 data_type을 함께 사용할 수 없음")
        parameters["data_type"] = parameters.pop("type")
    if normalized.get("type") == "aggregate" and "aggregation" in parameters:
        if "metrics" in parameters:
            raise ValueError("aggregate parameters에 aggregation과 metrics를 함께 사용할 수 없음")
        aggregation = parameters.pop("aggregation")
        if isinstance(aggregation, dict) and {
            "column", "function", "target"
        }.issubset(aggregation):
            aggregation = [aggregation]
        if not isinstance(aggregation, list):
            raise ValueError("aggregate aggregation 별칭은 metric 객체 또는 배열이어야 함")
        parameters["metrics"] = aggregation
    normalized["parameters"] = parameters
    return normalized


def _step_summary(result: dict) -> dict:
    operations = result.get("operations") or []
    return {
        "operation_count": len(operations),
        "operation_types": [item.get("type") for item in operations],
    }


def _run_prompt_step(
    *,
    system_prompt: str,
    request_payload: dict,
    validate,
    step_code: str,
    step_label: str,
    on_step=None,
) -> dict:
    if on_step is not None:
        on_step(step_code, "RUNNING", None)
    retry_feedback = None
    last_error = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            request = {**request_payload, "retry_feedback": retry_feedback}
            result = _extract_json(
                str(build_agent(system_prompt)(json.dumps(request, ensure_ascii=False)))
            )
            validate(result)
        except Exception as exc:  # noqa: BLE001 - 계약 실패는 현재 단계 안에서 재시도
            last_error = str(exc)
            retry_feedback = (
                f"직전 {attempt}회차 {step_label} 검증 실패: {last_error}. "
                "현재 단계 JSON만 수정해 다시 생성하세요."
            )
        else:
            if on_step is not None:
                on_step(step_code, "COMPLETED", _step_summary(result))
            return result
    if on_step is not None:
        on_step(
            step_code,
            "FAILED",
            {"validation_errors": [last_error] if last_error else []},
        )
    raise ValueError(f"{step_label} 단계를 {MAX_ATTEMPTS}회 생성했지만 실패했습니다: {last_error}")


def create_processing_plan(payload: dict, on_step=None) -> dict:
    """실제 행을 제외하고 네 단계 결과를 기존 ProcessingPlan으로 조립한다."""
    common = {
        "raw_requirement": payload.get("raw_requirement", ""),
        "analysis": payload.get("analysis") or {},
        "approved_selection": payload.get("selection") or {},
        "hitl_feedback": payload.get("hitl_feedback"),
    }
    selection = common["approved_selection"]
    accumulated: list[ProcessingOperation] = []

    def run_operation_step(prompt, code, label, allowed, extra):
        result = _run_prompt_step(
            system_prompt=prompt,
            request_payload={**common, **extra},
            validate=lambda value: _validate_operations_result(
                value,
                allowed_types=allowed,
                previous_operations=accumulated,
                selection=selection,
            ),
            step_code=code,
            step_label=label,
            on_step=on_step,
        )
        operations = [ProcessingOperation.model_validate(item) for item in result["operations"]]
        accumulated.extend(operations)
        return result

    dedup = run_operation_step(
        DEDUPLICATION_PLAN_PROMPT,
        "DEDUPLICATION_PLAN",
        "중복 제거 계획",
        {"deduplicate"},
        {},
    )
    missing = run_operation_step(
        MISSING_VALUE_PLAN_PROMPT,
        "MISSING_VALUE_PLAN",
        "결측 처리 계획",
        {"cast", "fill_missing"},
        {"deduplication_plan": dedup},
    )
    derived = run_operation_step(
        DERIVED_COLUMN_ORDER_PROMPT,
        "DERIVED_COLUMN_ORDER",
        "파생 컬럼 생성 순서 결정",
        {"derive_date_part", "bucketize", "aggregate", "sort"},
        {"deduplication_plan": dedup, "missing_value_plan": missing},
    )

    final_result = _run_prompt_step(
        system_prompt=FINAL_COLUMN_VALIDATION_PROMPT,
        request_payload={
            **common,
            "deduplication_plan": dedup,
            "missing_value_plan": missing,
            "derived_column_order": derived,
        },
        validate=lambda value: _validate_final_result(value, accumulated, selection),
        step_code="FINAL_COLUMN_VALIDATION",
        step_label="최종 컬럼·품질 검증 정의",
        on_step=on_step,
    )
    final_operations = [
        ProcessingOperation.model_validate(item) for item in final_result["operations"]
    ]
    plan_data = {**final_result, "operations": [
        operation.model_dump(mode="json") for operation in [*accumulated, *final_operations]
    ]}
    plan = ProcessingPlan.model_validate(plan_data)
    validate_processing_plan(plan, selection)
    return plan.model_dump(mode="json")


def _validate_final_result(
    result: dict,
    previous_operations: list[ProcessingOperation],
    selection: dict,
) -> None:
    expected = {
        "plan_version", "objective", "operations", "output", "quality_checks", "explanation"
    }
    if set(result) != expected:
        raise ValueError("최종 단계 결과 키가 계약과 일치하지 않음")
    final_operations = _validate_operations_result(
        {"operations": result["operations"]},
        allowed_types={"select_columns"},
        previous_operations=previous_operations,
        selection=selection,
    )
    plan = ProcessingPlan.model_validate(
        {
            **result,
            "operations": [
                operation.model_dump(mode="json")
                for operation in [*previous_operations, *final_operations]
            ],
        }
    )
    validate_processing_plan(plan, selection)
