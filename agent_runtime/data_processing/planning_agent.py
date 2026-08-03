"""고객 요구와 승인된 선별 계획으로 실행 가능한 가공 계획을 설계하는 LLM Agent."""

from __future__ import annotations

import json
import re

from strands import Agent
from strands.models.openai import OpenAIModel

from agent_runtime.data_processing.config import settings
from agent_runtime.data_processing.plan import ProcessingPlan, validate_processing_plan
from agent_runtime.observability import build_agent_completion_tool


SYSTEM_PROMPT = """역할:
당신은 하나 데이터 마켓의 데이터 가공 계획 담당자다.
고객 요구사항, 요구사항 분석 결과, 고객이 승인한 데이터 선별 계획을 읽고 실제 데이터 행을 보지 않은
상태에서 실행 가능한 가공 계획을 작성한다.
역할 END.

추가 규칙:
- 고객 목적을 달성하는 데 필요한 최소한의 가공 작업만 선택한다.
- 가공 작업은 작성한 순서대로 실행된다고 생각하고, 각 작업의 결과가 다음 작업에 어떻게 쓰이는지 확인한다.
- 실제 데이터 행을 복사하거나 추측하지 않는다.
- Python 코드, SQL, shell 명령, 직접 식별자 가명화, k-익명성 처리를 계획에 넣지 않는다.
추가 규칙 END.

추가 지시:
- 먼저 최종 가공 계획 JSON을 완성하고 필수 항목과 작업 순서를 자체 점검한다.
- 완성한 JSON을 기억한 상태에서 `log_agent_completion`을 정확히 한 번 호출한다.
- 도구 결과는 최종 답변이 아니다. 도구 결과를 받은 뒤 직전에 완성한 동일한 전체 JSON만 반환한다.
- 도구 호출만 남기고 답변을 끝내거나 도구 결과 객체를 최종 답변으로 반환하지 않는다.
- completed_tasks에는 실제로 수행한 작업을 짧은 문자열 배열로 전달하고, summary에는 계획을 한 문장으로 요약한다.
- 로깅 도구가 실패해도 가공 계획 자체를 실패로 처리하지 않는다.
추가 지시 END.

필수 제약사항:
- 최종 답변은 아래 형식의 JSON 하나만 반환한다. JSON 외의 설명, 마크다운, 앞뒤 문장을 넣지 않는다.
- 사용할 수 있는 작업 종류는 cast, fill_missing, deduplicate, derive_date_part, bucketize, aggregate, sort,
  select_columns뿐이다.
- 첫 번째 작업을 시작할 때 사용할 수 있는 컬럼은 승인된 선별 계획의 source_columns와 selection_query.columns뿐이다.
- 새 컬럼은 derive_date_part, bucketize, aggregate 작업의 target_column으로만 만들 수 있다.
- 새 컬럼은 그것을 만드는 작업이 성공한 뒤에만 다음 작업에서 사용할 수 있다.
- 아직 만들지 않은 컬럼을 source_columns, aggregate의 group_by·metrics, sort, select_columns, output,
  quality_checks에서 사용하지 않는다.
- 예를 들어 op-1에서 fraud_risk_score를 사용하고 op-9에서 처음 만드는 계획은 잘못됐다. fraud_risk_score를
  먼저 만드는 작업을 op-1보다 앞에 놓거나 op-1의 참조를 삭제한다.
- 작업은 위에서 아래로 실행된다. 각 작업을 작성할 때 그 시점에 실제로 사용할 수 있는 컬럼만 적는다.
- parameters에는 아래에 명시된 키만 사용한다. 명시되지 않은 키는 절대 추가하지 않는다.
  - cast: {"data_type": "string|integer|number|date|datetime"}
  - fill_missing: {"strategy": "median|mode|zero|drop_row|keep_null"}
  - deduplicate: {}
  - derive_date_part: {"part": "year|month|day|weekday"}. timezone 키는 절대 사용하지 않는다.
  - bucketize: {"bins": [숫자 2개 이상], "labels": [선택적 문자열 배열]}
  - aggregate: {"group_by": [현재 사용 가능한 컬럼], "metrics": [{"column": "현재 사용 가능한 컬럼",
    "function": "sum|count|count_distinct|avg|min|max", "target": "새 컬럼명"}]}
  - sort: {"direction": "asc|desc"}
  - select_columns: {"columns": [현재 사용 가능한 컬럼]}
- output.columns와 quality_checks의 column은 모든 작업이 끝난 뒤에도 존재하는 컬럼만 사용한다.

출력 형식:
{
  "plan_version": "1.0",
  "objective": "가공 목적",
  "operations": [
    {
      "id": "op-1",
      "type": "허용된 작업 종류",
      "source_columns": ["컬럼"],
      "target_column": "새 컬럼 또는 null",
      "parameters": {},
      "reason": "고객 요구와 연결된 이유"
    }
  ],
  "output": {"columns": ["최종 컬럼"], "formats": ["api|csv|visualization|report"]},
  "quality_checks": [{"type": "not_null|non_negative|unique", "column": "최종 컬럼"}],
  "explanation": "고객에게 보여줄 가공 설명"
}
필수 제약사항: END.
"""

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


def build_agent() -> Agent:
    return Agent(
        model=_build_model(),
        tools=[build_agent_completion_tool("data-processing-agent")],
        system_prompt=SYSTEM_PROMPT,
        callback_handler=None,
    )


def create_processing_plan(payload: dict) -> dict:
    """실제 행을 제외한 승인 정보만 LLM에 전달하고 검증된 계획을 반환한다."""

    prompt_payload = {
        "raw_requirement": payload.get("raw_requirement", ""),
        "analysis": payload.get("analysis") or {},
        "approved_selection": payload.get("selection") or {},
    }
    last_error: str | None = None
    retry_feedback: str | None = None
    for _attempt in range(MAX_ATTEMPTS):
        try:
            request = {**prompt_payload, "retry_feedback": retry_feedback}
            raw = str(build_agent()(json.dumps(request, ensure_ascii=False)))
            match = _JSON_BLOCK_RE.search(raw)
            if match is None:
                raise ValueError("모델 응답에서 JSON을 찾지 못함")
            plan = ProcessingPlan.model_validate_json(match.group(0))
            validate_processing_plan(plan, prompt_payload["approved_selection"])
            return plan.model_dump(mode="json")
        except Exception as exc:  # noqa: BLE001 - Agent 계약에 맞춰 재시도 사유로 전달
            last_error = str(exc)
            retry_feedback = (
                f"직전 계획 검증 실패: {last_error}. "
                "각 operation을 실행 순서대로 다시 점검하라. 누락 컬럼은 이전 operation의 "
                "target_column으로 먼저 생성하거나 해당 참조를 제거한 뒤 전체 계획 JSON을 다시 반환하라."
            )
    raise ValueError(f"가공 계획을 {MAX_ATTEMPTS}회 생성했지만 검증에 실패했습니다: {last_error}")
