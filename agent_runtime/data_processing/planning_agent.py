"""고객 요구와 승인된 선별 계획으로 실행 가능한 가공 계획을 설계하는 LLM Agent."""

from __future__ import annotations

import json
import re

from strands import Agent
from strands.models.openai import OpenAIModel

from agent_runtime.data_processing.config import settings
from agent_runtime.data_processing.plan import ProcessingPlan, validate_processing_plan


SYSTEM_PROMPT = """당신은 하나 데이터 마켓의 데이터 가공 계획 Agent다.
고객 요구사항, 요구사항 분석 결과, 고객이 승인한 데이터 선별 계획을 읽고 실제 데이터 행을 보지 않은
상태에서 실행 가능한 가공 계획을 설계한다.

반드시 JSON 하나만 반환한다. 임의 Python 코드, SQL, shell 명령은 만들지 않는다.
사용 가능한 operation은 cast, fill_missing, deduplicate, derive_date_part, bucketize, aggregate, sort,
select_columns뿐이다. source_columns는 승인된 선별 계획에서 현재 사용 가능한 컬럼만 사용한다.
새 컬럼은 derive_date_part, bucketize 또는 aggregate의 target으로만 생성한다.
직접 식별자 가명화와 k-익명성은 실행기가 강제하므로 계획에 넣거나 기준을 변경하지 않는다.
고객 목적을 달성하는 데 필요한 최소 operation만 선택하고 실행 순서대로 배열에 넣는다.

출력 형식:
{
  "plan_version": "1.0",
  "objective": "가공 목적",
  "operations": [
    {
      "id": "op-1",
      "type": "허용 operation",
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
    return Agent(model=_build_model(), tools=[], system_prompt=SYSTEM_PROMPT, callback_handler=None)


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
            retry_feedback = last_error
    raise ValueError(f"가공 계획을 {MAX_ATTEMPTS}회 생성했지만 검증에 실패했습니다: {last_error}")
