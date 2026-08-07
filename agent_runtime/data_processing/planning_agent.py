"""승인된 선별 계획으로 4단계 가공 계획을 설계하는 LLM Agent."""

from __future__ import annotations

import json
import re
import time
from copy import deepcopy

from strands import Agent
from strands.models.openai import OpenAIModel

from agent_runtime.data_processing.config import settings
from agent_runtime.data_processing.plan import (
    ProcessingOperation,
    ProcessingPlan,
    order_derived_operations,
    validate_processing_operations,
    validate_processing_plan,
    validate_approved_derived_coverage,
)


COMMON_RULES = """실제 데이터 행은 제공되지 않으며 보려고 시도해서도 안 된다.
승인된 선별 계획의 컬럼만 사용한다. 임의 Python 코드, SQL, shell 명령을 만들지 않는다.
직접 식별자 가명화와 k-익명성은 결정론적 실행기가 강제하므로 기준을 변경하지 않는다.
hitl_feedback이 있으면 승인 범위 안에서 최종 반려 의견을 우선 반영한다.
JSON 하나 외에는 출력하지 않는다."""

EXPRESSION_OPERAND_RULES = """expression 피연산자는 문자열·숫자·boolean을 직접 쓰지 않고
반드시 다음 객체 중 하나로 표현한다.
- 컬럼 참조: {"column":"승인된 원본 또는 앞에서 생성한 파생 컬럼"}
- 상수: {"literal":"고정값"}
- 중첩식: {"operation":"compare|logical|conditional|arithmetic|map_values",
  "parameters":{...}}
compare의 left/right, logical의 operands 각 항목, conditional의 condition/true_value/
false_value, arithmetic의 operands 각 항목, map_values의 source/default에 모두 이 규칙을
적용한다. 예: {"operator":"gte","left":{"column":"amount"},
"right":{"literal":10000}}. 상수 문자열도 반드시 {"literal":"고액"}처럼 감싼다."""

DEDUPLICATION_PLAN_PROMPT = f"""역할:
데이터 가공 Agent의 중복 제거 계획 단계다. 고객 요구와 승인된 선별 계획을 근거로 중복 판단
컬럼만 결정하며, 다른 가공 단계의 operation은 만들지 않는다.
역할END.

규칙:
- 중복 제거가 필요하면 판단 근거가 되는 승인 컬럼을 source_columns에 넣는다.
- 중복 제거가 불필요하면 operations를 빈 배열로 반환한다.
- 앞뒤 단계의 계획을 변경하거나 추가하지 않는다.
규칙END.

제약사항:
- 허용 operation은 deduplicate 하나뿐이다.
- target_column은 null이고 parameters는 빈 객체다.
- 승인된 선별 계획의 컬럼만 사용한다.
제약사항END.

출력형식:
JSON 하나만 출력한다. 부수적인 설명, Markdown, 코드 블록을 출력하지 않는다.
{{"operations":[{{"id":"dedup-1","type":"deduplicate","source_columns":["컬럼"],
"target_column":null,"parameters":{{}},"reason":"근거"}}]}}
출력형식END.

긍정 강화:
승인 컬럼과 목적에 근거한 최소한의 중복 제거 계획은 후속 가공의 신뢰도를 높입니다. 계약을
점검한 뒤 JSON 하나만 반환하세요.
긍정 강화END.

{COMMON_RULES}"""

MISSING_VALUE_PLAN_PROMPT = f"""역할:
데이터 가공 Agent의 결측 처리 계획 단계다. 검증된 deduplication_plan을 바꾸지 않고 승인
컬럼의 형 변환과 결측 처리 operation만 설계한다.
역할END.

규칙:
- 결측 처리와 형 변환이 불필요하면 operations를 빈 배열로 반환한다.
- 필요한 입력 컬럼은 source_columns에 넣고, 각 operation의 근거를 reason에 적는다.
- 앞 단계의 중복 제거 계획과 이후 파생·최종 검증 계획은 변경하지 않는다.
규칙END.

제약사항:
- 허용 operation은 cast와 fill_missing뿐이다.
- fill_missing strategy는 median, mode, zero, drop_row, keep_null 중 하나다.
- cast parameters는 {{"data_type":"string|integer|number|date|datetime"}} 형식만 사용한다.
- 승인된 선별 계획의 컬럼만 사용한다.
제약사항END.

출력형식:
JSON 하나만 출력한다. 부수적인 설명, Markdown, 코드 블록을 출력하지 않는다.
{{"operations":[{{"id":"missing-1","type":"fill_missing","source_columns":["컬럼"],
"target_column":null,"parameters":{{"strategy":"keep_null"}},"reason":"근거"}}]}}
출력형식END.

긍정 강화:
명확한 결측 처리와 형 변환 계획은 다음 파생 계산을 안정적으로 만듭니다. 허용값과 필수 키를
점검한 뒤 JSON 하나만 반환하세요.
긍정 강화END.

{COMMON_RULES}"""

DERIVED_COLUMN_ORDER_PROMPT = f"""역할:
승인된 파생 컬럼 정의를 데이터 가공 executor가 실행할 수 있는 operation으로 변환하고,
컬럼 의존 관계에 맞는 생성 순서를 결정하는 Agent다.
역할END.

규칙:
- approved_selection.derived_columns의 각 name을 target_column으로 정확히 한 번 생성한다.
- derivation_spec의 분석 의미와 evidence는 유지하되, parameters를 그대로 복사하지 말고
  아래 executor 계약에 맞게 변환한다.
- 원본 컬럼과 앞 operation에서 만든 target_column만 참조한다.
- 의존 대상 컬럼을 먼저 생성하도록 operations 배열 순서를 정한다.
- 입력 컬럼은 parameters가 아니라 source_columns에 넣는다.
- source_columns에는 expression에서 실제 참조하는 컬럼만 중복 없이 넣는다.
- 파생 집계는 원본 행을 유지하는 window_aggregate를 우선 사용한다.
- Python, SQL, shell, 허용 목록 밖 operation을 만들지 않는다.
규칙END.

제약사항:
- 허용 operation: derive_date_part, bucketize, compare, logical, conditional,
  arithmetic, map_values, window_aggregate, aggregate, sort.
- bucketize parameters: bins, labels만 사용한다. source는 넣지 않는다.
- map_values parameters: source, mapping, default만 사용한다.
  source는 반드시 {{"column":"컬럼"}} 객체이며 생략할 수 없다.
- compare: operator, left, right만 사용한다.
- window_aggregate와 aggregate:
  {{"group_by":["컬럼"],"metrics":[{{"column":"컬럼",
  "function":"sum|count|count_distinct|avg|min|max","target":"새컬럼"}}]}}
  형식만 사용한다.
- group_by와 metrics.column은 객체가 아닌 컬럼명 문자열이다.
- derive_date_part parameters: part, timezone만 사용한다.
- sort parameters: direction만 사용한다.
- 모든 expression operand는 아래 객체 형식만 사용한다.
  - 컬럼: {{"column":"컬럼"}}
  - 상수: {{"literal":"값"}}
  - 중첩식: {{"operation":"compare|logical|conditional|arithmetic|map_values",
    "parameters":{{...}}}}
제약사항END.

출력형식:
JSON 하나만 출력한다. 부수적인 설명, Markdown, 코드 블록을 출력하지 않는다.
{{
  "operations": [
    {{
      "id": "derived-1",
      "type": "map_values",
      "source_columns": ["mcc_code"],
      "target_column": "mcc_name",
      "parameters": {{
        "source": {{"column":"mcc_code"}},
        "mapping": {{"5411":"마트/슈퍼마켓"}},
        "default": {{"literal":null}}
      }},
      "reason": "승인된 업종 코드-명칭 변환"
    }}
  ]
}}
출력형식END.

긍정 강화:
승인된 의미를 정확히 보존하면서 executor 계약까지 충족한 간결한 operation 계획을 만들면,
후속 가공과 검증이 안정적으로 완료됩니다. 모든 필수 키와 객체 형식을 최종 점검한 뒤
JSON 하나만 반환하세요.
긍정 강화END.

{COMMON_RULES}"""

FINAL_COLUMN_VALIDATION_PROMPT = f"""역할:
데이터 가공 Agent의 최종 컬럼·품질 검증 정의 단계다. 앞 단계 operation을 변경하지 않고,
최종 출력 컬럼·형식·품질 검증을 확정한다.
역할END.

규칙:
- 마지막에 select_columns operation을 정확히 하나 만든다.
- output.columns와 select_columns.parameters.columns는 동일한 최종 컬럼을 사용한다.
- quality_checks는 최종 출력 컬럼에만 적용하며 각 검증의 근거를 목적에 맞춘다.
- 앞의 중복·결측·파생 operation을 변경하거나 새 가공 operation을 만들지 않는다.
규칙END.

제약사항:
- 이 단계의 허용 operation은 select_columns 하나뿐이다.
- output.formats는 api, csv, visualization, report만 사용한다.
- quality_checks.type은 not_null, non_negative, unique만 사용한다.
- 승인된 선별 계획 및 앞 operation에서 사용 가능한 컬럼만 최종 출력에 넣는다.
제약사항END.

출력형식:
JSON 하나만 출력한다. 부수적인 설명, Markdown, 코드 블록을 출력하지 않는다.
{{
  "plan_version":"1.0",
  "objective":"가공 목적",
  "operations":[{{"id":"final-1","type":"select_columns","source_columns":[],
    "target_column":null,"parameters":{{"columns":["최종 컬럼"]}},"reason":"근거"}}],
  "output":{{"columns":["최종 컬럼"],"formats":["api|csv|visualization|report"]}},
  "quality_checks":[{{"type":"not_null|non_negative|unique","column":"최종 컬럼"}}],
  "explanation":"고객에게 보여줄 설명"
}}
출력형식END.

긍정 강화:
일관된 최종 컬럼과 품질 검증 정의는 결과 파일과 고객 검토를 신뢰할 수 있게 만듭니다. 모든
필수 키와 허용값을 점검한 뒤 JSON 하나만 반환하세요.
긍정 강화END.

{COMMON_RULES}"""

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
MAX_ATTEMPTS = 3


class ProcessingPlanningError(ValueError):
    """가공 계획 실패 원인과 마지막 안전한 후보 JSON을 함께 전달한다."""

    def __init__(self, message: str, failure_snapshot: dict):
        super().__init__(message)
        self.failure_snapshot = failure_snapshot


def _build_model() -> OpenAIModel:
    return OpenAIModel(
        client_args={
            "api_key": settings.deepseek_api_key,
            "base_url": settings.deepseek_base_url,
            "timeout": settings.data_processing_model_timeout_seconds,
            "max_retries": 0,
        },
        model_id=settings.data_processing_model_id,
        params={"temperature": 0, "response_format": {"type": "json_object"}},
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
    order_by_dependency: bool = False,
    validate_coverage: bool = False,
) -> list[ProcessingOperation]:
    if set(result) != {"operations"} or not isinstance(result["operations"], list):
        raise ValueError("현재 단계 결과는 operations 배열 하나만 포함해야 함")
    normalized_items = [_normalize_operation_item(item) for item in result["operations"]]
    result["operations"] = normalized_items
    operations = [ProcessingOperation.model_validate(item) for item in normalized_items]
    invalid = {operation.type for operation in operations} - allowed_types
    if invalid:
        raise ValueError(f"현재 단계에 허용되지 않은 operation: {', '.join(sorted(invalid))}")
    if order_by_dependency:
        operations = order_derived_operations(operations, selection)
        result["operations"] = [operation.model_dump(mode="json") for operation in operations]
    combined = [*previous_operations, *operations]
    identifiers = [operation.id for operation in combined]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("processing operation ids must be unique")
    validate_processing_operations(combined, selection)
    if validate_coverage:
        validate_approved_derived_coverage(combined, selection)
    return operations


def _normalize_operation_item(item: dict) -> dict:
    """LLM의 의미가 단일하게 결정되는 별칭만 실행 계약으로 정규화한다."""
    if not isinstance(item, dict):
        raise ValueError("processing operation은 JSON 객체여야 함")
    normalized = dict(item)
    parameters = dict(normalized.get("parameters") or {})
    if normalized.get("type") == "aggregate" and normalized.get("target_column"):
        # 승인된 '파생 컬럼' 집계는 행 축약이 아니라 그룹 통계를 각 행에 붙이는 의미다.
        # LLM이 legacy aggregate를 반환해도 실행 의미가 단일한 경우 안전하게 정규화한다.
        normalized["type"] = "window_aggregate"
    aliases = {
        "==": "eq", "=": "eq", "!=": "neq", "<>": "neq",
        ">": "gt", ">=": "gte", "<": "lt", "<=": "lte",
    }

    def normalize_operators(value) -> None:
        if isinstance(value, dict):
            if value.get("operator") in aliases:
                value["operator"] = aliases[value["operator"]]
            for nested in value.values():
                normalize_operators(nested)
        elif isinstance(value, list):
            for nested in value:
                normalize_operators(nested)

    normalize_operators(parameters)

    def normalize_expression_aliases(value) -> None:
        if not isinstance(value, dict):
            if isinstance(value, list):
                for nested in value:
                    normalize_expression_aliases(nested)
            return
        operation = value.get("operation") or (normalized.get("type") if value is normalized else None)
        expression_parameters = value.get("parameters")
        if not isinstance(expression_parameters, dict):
            expression_parameters = parameters if value is normalized else None
        if isinstance(expression_parameters, dict):
            if operation == "logical" and "conditions" in expression_parameters:
                if "operands" in expression_parameters:
                    raise ValueError("logical parameters에 conditions와 operands를 함께 사용할 수 없음")
                expression_parameters["operands"] = expression_parameters.pop("conditions")
            if operation == "conditional" and any(
                key in expression_parameters for key in ("if", "then", "else")
            ):
                aliases = {"if": "condition", "then": "true_value", "else": "false_value"}
                for old, new in aliases.items():
                    if old in expression_parameters:
                        if new in expression_parameters:
                            raise ValueError(f"conditional parameters에 {old}와 {new}를 함께 사용할 수 없음")
                        expression_parameters[new] = expression_parameters.pop(old)
        for nested in value.values():
            normalize_expression_aliases(nested)

    normalize_expression_aliases(normalized)
    operation_type = normalized.get("type")
    source_columns = list(normalized.get("source_columns") or [])
    if "column" in parameters:
        column = parameters.get("column")
        if not isinstance(column, str) or not column:
            raise ValueError(f"{operation_type} parameters.column은 비어 있지 않은 문자열이어야 함")

        if operation_type in {"sort", "derive_date_part", "bucketize"}:
            if source_columns and source_columns != [column]:
                raise ValueError(
                    f"{operation_type} parameters.column이 source_columns와 충돌함"
                )
            normalized["source_columns"] = [column]
            parameters.pop("column")
        elif operation_type == "compare":
            if "left" in parameters or "right" in parameters:
                raise ValueError(
                    "compare parameters에 column/value와 left/right를 함께 사용할 수 없음"
                )
            if "value" not in parameters:
                raise ValueError("compare parameters.column 별칭에는 value가 함께 필요함")
            if source_columns and source_columns != [column]:
                raise ValueError("compare parameters.column이 source_columns와 충돌함")
            normalized["source_columns"] = [column]
            parameters["left"] = {"column": column}
            parameters["right"] = {"literal": parameters.pop("value")}
            parameters.pop("column")
        elif operation_type in {"aggregate", "window_aggregate"}:
            raise ValueError(
                "aggregate parameters.column은 의미가 모호함; group_by와 metrics 계약을 사용해야 함"
            )
    if normalized.get("type") == "cast" and "type" in parameters:
        if "data_type" in parameters:
            raise ValueError("cast parameters에 type과 data_type을 함께 사용할 수 없음")
        parameters["data_type"] = parameters.pop("type")
    if normalized.get("type") in {"aggregate", "window_aggregate"} and "aggregation" in parameters:
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

    expression_columns = {
        str(column) for column in normalized.get("source_columns") or [] if column
    }

    def normalize_operand(value):
        if isinstance(value, dict):
            nested_operation = value.get("operation")
            nested_parameters = value.get("parameters")
            if set(value) == {"operation", "parameters"} and isinstance(
                nested_operation, str
            ) and isinstance(nested_parameters, dict):
                normalize_expression_operands(nested_operation, nested_parameters)
            return value
        if isinstance(value, str) and value in expression_columns:
            return {"column": value}
        if isinstance(value, (str, int, float, bool)) or value is None or isinstance(value, list):
            return {"literal": value}
        return value

    def normalize_expression_operands(operation: str, expression: dict) -> None:
        if operation == "compare":
            for key in ("left", "right"):
                if key in expression:
                    expression[key] = normalize_operand(expression[key])
        elif operation in {"logical", "arithmetic"}:
            operands = expression.get("operands")
            if isinstance(operands, list):
                expression["operands"] = [normalize_operand(value) for value in operands]
        elif operation == "conditional":
            for key in ("condition", "true_value", "false_value"):
                if key in expression:
                    expression[key] = normalize_operand(expression[key])
        elif operation == "map_values":
            if "source" in expression:
                expression["source"] = normalize_operand(expression["source"])
            if "default" in expression:
                expression["default"] = normalize_operand(expression["default"])

    normalize_expression_operands(str(normalized.get("type") or ""), parameters)
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
    on_log=None,
) -> dict:
    if on_step is not None:
        on_step(step_code, "RUNNING", None)
    retry_feedback = None
    last_error = None
    last_candidate = None
    last_raw_candidate = None
    last_candidate_attempt = None
    last_response_excerpt = None
    last_response_length = 0
    last_finish_reason = None
    attempt_diagnostics: list[dict] = []
    attempts_used = 0
    for attempt in range(1, MAX_ATTEMPTS + 1):
        attempts_used = attempt
        parsed = False
        started = time.monotonic()
        try:
            request = {**request_payload, "retry_feedback": retry_feedback}
            raw = build_agent(system_prompt)(json.dumps(request, ensure_ascii=False))
            raw_text = str(raw)
            last_response_length = len(raw_text)
            last_response_excerpt = raw_text[:2000] if raw_text else None
            finish_reason = getattr(raw, "stop_reason", None)
            last_finish_reason = str(finish_reason) if finish_reason else None
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if on_log is not None:
                on_log(
                    "INFO",
                    f"{step_label} LLM 응답 수신 ({attempt}/{MAX_ATTEMPTS}회차, "
                    f"{elapsed_ms}ms, {last_response_length}자)",
                    {
                        "step": step_code,
                        "attempt": attempt,
                        "elapsed_ms": elapsed_ms,
                        "response_length": last_response_length,
                        "finish_reason": last_finish_reason,
                    },
                )
            result = _extract_json(raw_text)
            parsed = True
            last_raw_candidate = deepcopy(result)
            last_candidate = result
            last_candidate_attempt = attempt
            validate(result)
        except Exception as exc:  # noqa: BLE001 - 계약 실패는 현재 단계 안에서 재시도
            last_error = str(exc)
            attempt_diagnostics.append(
                {
                    "attempt": attempt,
                    "error": last_error,
                    "response_length": last_response_length,
                    "finish_reason": last_finish_reason,
                    "json_parsed": parsed,
                    "candidate": deepcopy(last_candidate) if parsed else None,
                }
            )
            # 재시도는 실패가 아니지만 왜 다시 도는지는 화면에 보여야 한다 — 안 그러면
            # 실무자 눈에는 몇 분간 아무 일도 안 일어나는 것처럼 보인다.
            if on_log is not None:
                remaining = MAX_ATTEMPTS - attempt
                on_log(
                    "WARN" if remaining > 0 else "ERROR",
                    f"{step_label} 검증 실패 ({attempt}/{MAX_ATTEMPTS}회차): {last_error}"
                    + (" — 재시도합니다." if remaining > 0 else ""),
                    {
                        "step": step_code,
                        "attempt": attempt,
                        "validation_error": last_error,
                        "json_parsed": parsed,
                        "retry_hint": _contract_retry_hint(last_error),
                    },
                )
            retry_feedback = (
                f"직전 {attempt}회차 {step_label} 검증 실패: {last_error}. "
                f"{_contract_retry_hint(last_error)}"
                "현재 단계 JSON만 수정해 다시 생성하세요."
            )
        else:
            if on_step is not None:
                on_step(step_code, "COMPLETED", _step_summary(result))
            return result
    if on_step is not None:
        failure_snapshot = {
            "failed_step": step_code,
            "candidate_processing_plan": last_candidate,
            "raw_candidate_processing_plan": last_raw_candidate,
            "candidate_attempt": last_candidate_attempt,
            "validation_errors": [last_error] if last_error else [],
            "retry_count": attempts_used,
            "failure_code": "PROCESSING_RULE_INVALID",
            "model_response": {
                "length": last_response_length,
                "excerpt": last_response_excerpt,
                "finish_reason": last_finish_reason,
            },
            "attempt_diagnostics": attempt_diagnostics,
        }
        on_step(
            step_code,
            "FAILED",
            failure_snapshot,
        )
    else:
        failure_snapshot = {
            "failed_step": step_code,
            "candidate_processing_plan": last_candidate,
            "raw_candidate_processing_plan": last_raw_candidate,
            "candidate_attempt": last_candidate_attempt,
            "validation_errors": [last_error] if last_error else [],
            "retry_count": attempts_used,
            "failure_code": "PROCESSING_RULE_INVALID",
            "model_response": {
                "length": last_response_length,
                "excerpt": last_response_excerpt,
                "finish_reason": last_finish_reason,
            },
            "attempt_diagnostics": attempt_diagnostics,
        }
    raise ProcessingPlanningError(
        f"{step_label} 단계를 {MAX_ATTEMPTS}회 생성했지만 실패했습니다: {last_error}",
        failure_snapshot,
    )


def _contract_retry_hint(error: str) -> str:
    if "expression operand" in error:
        return (
            "모든 expression 피연산자를 객체로 고치세요. 컬럼은 {\"column\":\"컬럼\"}, "
            "상수는 {\"literal\":값} 형식입니다. compare.left/right, logical.operands, "
            "conditional.condition/true_value/false_value, arithmetic.operands, "
            "map_values.source/default에 문자열·숫자·boolean을 직접 넣지 마세요. "
        )
    if "unsupported parameters" in error or "parameters.column" in error:
        return (
            "허용되지 않은 parameter를 제거하세요. 입력 컬럼은 source_columns에 넣고, "
            "sort는 parameters={\"direction\":\"asc|desc\"}, derive_date_part는 "
            "parameters={\"part\":\"year|month|day|weekday|hour\","
            "\"timezone\":\"UTC|Asia/Seoul\"}, compare는 operator/left/right만, "
            "aggregate는 group_by/metrics만 사용하세요. "
        )
    if "operator is invalid" in error or "operation is invalid" in error:
        return (
            "허용 operator만 사용하세요. compare는 eq|neq|gt|gte|lt|lte|in, "
            "logical은 and|or|not, arithmetic은 add|subtract|multiply|divide입니다. "
        )
    if "exactly match source_columns" in error:
        return (
            "expression 안의 모든 column 참조와 source_columns를 중복 없이 정확히 "
            "일치시키고, 상수는 source_columns에 넣지 마세요. "
        )
    if "unavailable columns" in error or "unknown columns" in error:
        return (
            "승인된 source_columns 또는 앞 operation에서 이미 생성된 target_column만 "
            "참조하고 의존 순서대로 operations를 배열하세요. "
        )
    if "approved derived columns" in error or "unapproved derived columns" in error:
        return (
            "approved_selection.derived_columns의 name을 각각 정확히 한 번 생성하고, "
            "승인되지 않은 target_column은 만들지 마세요. "
        )
    if "aggregate" in error:
        return (
            "aggregate parameters는 group_by 문자열 배열과 metrics 객체 배열을 사용하세요. "
            "각 metric은 column, function, target을 포함해야 합니다. "
        )
    if "must be a non-empty string list" in error:
        return "해당 필드는 비어 있지 않은 컬럼명 문자열 배열로 만드세요. "
    if "required" in error or "Field required" in error:
        return "반환 계약의 필수 키를 모두 포함하고 null 대신 요구된 타입을 사용하세요. "
    return "오류에 언급된 필드만 반환 계약에 맞게 고치고 승인된 의미는 변경하지 마세요. "


def create_processing_plan(payload: dict, on_step=None, on_log=None) -> dict:
    """실제 행을 제외하고 네 단계 결과를 기존 ProcessingPlan으로 조립한다."""
    common = {
        "raw_requirement": payload.get("raw_requirement", ""),
        "analysis": payload.get("analysis") or {},
        "approved_selection": payload.get("selection") or {},
        "hitl_feedback": payload.get("hitl_feedback"),
    }
    selection = common["approved_selection"]
    accumulated: list[ProcessingOperation] = []

    def run_operation_step(
        prompt, code, label, allowed, extra, *, order_by_dependency=False,
        validate_coverage=False,
    ):
        result = _run_prompt_step(
            system_prompt=prompt,
            request_payload={**common, **extra},
            validate=lambda value: _validate_operations_result(
                value,
                allowed_types=allowed,
                previous_operations=accumulated,
                selection=selection,
                order_by_dependency=order_by_dependency,
                validate_coverage=validate_coverage,
            ),
            step_code=code,
            step_label=label,
            on_step=on_step,
            on_log=on_log,
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
        {
            "derive_date_part", "bucketize", "compare", "logical", "conditional",
            "arithmetic", "map_values", "window_aggregate", "aggregate", "sort",
        },
        {"deduplication_plan": dedup, "missing_value_plan": missing},
        order_by_dependency=True,
        validate_coverage=True,
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
        on_log=on_log,
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
