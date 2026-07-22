"""데이터 선별 에이전트 — 독립 실행 모듈.

요구사항 분석 결과(analysis)와 지금 조회 가능한 가명화 데이터 소스 목록(available_data)을 읽어,
이 요청에 어떤 테이블이 필요하고 어떤 기준(임베딩 벡터 유사도 조회 조건)으로 데이터를 걸러낼지,
그리고 사람이 검토할 샘플 데이터에 어떤 컬럼이 나올지를 구조화한다. 이 에이전트 자신은 실제
DB나 임베딩을 조회하지 않는다(툴 없음) — "무엇을 어떻게 선별할지" 계획만 다음 단계(실제 조회/
가공)가 바로 실행할 수 있는 형태로 만들어 돌려준다.

이 모듈은 산출물이 다음 단계로 넘길만큼 괜찮은지(선택한 테이블이 실제로 존재하는지, top_k가
적절한지 등) 스스로 판단하지 않는다 — 모델을 호출하고 결과를 구조화해서 돌려주기만 한다.
그 판단(automation-supervisor-api의 validate_stage_output + 재시도/롤백 정책)은 오케스트레이션의
몫이다. agent_runtime/requirements_analysis/agent.py와 동일한 설계 원칙을 따른다.

이 모듈은 app/ 패키지(FastAPI)에 의존하지 않는다 — agent_runtime/requirements_analysis와 같은
이유로, 이 폴더(agent_runtime/data_selection/)만으로 완결되게 짠다.

`run()`은 @tool로 감싸져 있어 오케스트레이션 쪽 strands 에이전트가 표준 툴로 그대로 호출할 수
있다. `@tool`이 붙어도 그냥 파이썬 함수로 직접 호출 가능하다(콜러블 유지).
"""

import json
import re

from strands import Agent, tool
from strands.models.openai import OpenAIModel

from agent_runtime.data_selection.config import settings

SYSTEM_PROMPT = """당신은 '하나 데이터 마켓'의 데이터 선별 에이전트다.
이 서비스는 가명처리된 원본 데이터(회원정보/가맹점정보/거래내역 등)를 사용자의 요구사항 분석
결과에 맞춰 선별한다.

사용자 메시지는 아래 형식의 JSON 문자열로 주어진다:
{
  "raw_requirement": "사용자의 원본 자연어 요청",
  "analysis": {
    "usage_purpose": "...",
    "requested_data_sentence": "...",
    "categories": {"카테고리명": "값"},
    "delivery_channel": "...",
    "output_formats": ["..."]
  },
  "available_data": ["지금 조회 가능한 가명화 테이블 이름 목록"]
}

당신의 임무는 이 입력을 읽고 아래 3개 항목으로 구조화하는 것이다. 실제로 DB나 임베딩을 조회할
필요는 없다 — 그건 다음 단계가 한다. 임베딩 기반 벡터 유사도 조회가 가능하다고 가정한다.

반드시 아래 JSON 형식으로만 응답한다. 다른 설명, 코드블록 마크다운(```), 서두 문구를 절대
붙이지 않는다:

{
  "selected_tables": [{"table": "<available_data 중 하나>", "reason": "<이 테이블이 필요한 이유>"}],
  "selection_query": {
    "vector_similarity": true,
    "top_k": <조회할 표본 크기(정수, 기본 20)>,
    "filters": {<analysis.categories를 그대로 반영한 필터 조건 객체>}
  },
  "sample_columns": [
    {
      "name": "<사람이 검토할 샘플 데이터에 표시할 컬럼명>",
      "is_predicted": <원본 데이터에 그대로 있는 값이면 false, 계산/추정해야 하는 지표면 true>,
      "description": "<이 컬럼을 어떤 기준으로 선정했고 어떻게 산출하는지 한 줄 설명>"
    }
  ]
}

규칙:
- selected_tables에는 available_data에 실제로 있는 테이블 이름만 쓴다(환각 금지). available_data에
  없는 테이블을 지어내지 않는다.
- selection_query.filters는 analysis.categories에 실제로 있는 조건만 반영한다. 없는 조건을
  지어내지 않는다.
- sample_columns의 앞부분은 analysis.categories의 각 키를 "키 이름 그대로"(동의어나 다른 표현으로
  바꾸지 않고) 컬럼명으로 담는다. 순서는 categories에 나온 순서를 따른다. is_predicted는 항상
  false.
- 회원ID/고객ID 같은 식별자 컬럼은 raw_requirement나 analysis에 명시적으로 필요하다고 언급되지
  않는 한 추가하지 않는다.
- 그 다음, raw_requirement와 usage_purpose에 비추어 이 요청에 가장 핵심적인 지표를 정확히 2개
  선정해서 추가한다(1개도 3개도 안 되고 반드시 2개). 지표 이름은 반드시 selected_tables가 실제로
  담고 있는 정보(결제·거래 기록)로 표현 가능한 개념만 쓴다 — "방문", "방문빈도", "체류시간"처럼
  결제 기록만으로는 직접 확인할 수 없는 개념은 쓰지 않는다. 대신 "결제건수", "결제금액"처럼 거래
  기록 자체를 가리키는 표현을 쓴다. 개수는 항상 2개로 고정한다. 이 2개는 원본에 없고 계산·추정해야
  하는 지표이므로 is_predicted는 항상 true.
- 시간(월/일/분기/시간대 등) 관련 컬럼이 필요하면 요청에 가장 자연스러운 단위 하나만 고르고,
  여러 단위를 동시에 넣지 않는다. raw_requirement에 이미 특정 시간 단위 표현이 명시돼 있으면
  (예: "시간대별로") 그 표현을 그대로 컬럼명으로 재사용하고 축약하거나 다른 표현으로 바꾸지
  않는다. 이 컬럼은 원본에 있는 값이므로 is_predicted는 false.
- 모든 컬럼명은 띄어쓰기 없이 붙여 쓴다(예: "총결제금액", "결제건수". "총 결제 금액"처럼 띄어
  쓰지 않는다).
- 핵심 지표 컬럼명에는 "총"/"평균"/"합계" 같은 집계 접두어·접미어를 붙이지 않는다(예: "총결제금액"이
  아니라 "결제금액", "평균결제건수"가 아니라 "결제건수"). 집계 방식(합계인지 평균인지 등)은 컬럼명이
  아니라 description에서 설명한다.
- 핵심 지표 컬럼명에 이미 다른 컬럼(업종 등)에 담긴 정보를 반복해서 넣지 않는다(예: 업종 컬럼이
  이미 있는데 "여행결제건수"처럼 업종명을 지표 이름 앞에 다시 붙이지 않는다 — "결제건수"로 충분).
- JSON 외의 텍스트를 절대 출력하지 않는다."""

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)

# 모델 호출 또는 응답 파싱이 실패했을 때 이 안에서 흡수할 수 있는 재시도 횟수. 오케스트레이션의
# RETRY_SAME_STAGE는 이 3회를 다 써도 실패했을 때만 발동한다.
MAX_ATTEMPTS = 3


def _build_model() -> OpenAIModel:
    """현재는 DeepSeek(OpenAI SDK 호환 API)를 사용한다(requirements_analysis와 동일 자격증명)."""
    return OpenAIModel(
        client_args={
            "api_key": settings.deepseek_api_key,
            "base_url": settings.deepseek_base_url,
        },
        model_id=settings.data_selection_model_id,
        params={"temperature": 0},
    )


def build_agent() -> Agent:
    return Agent(model=_build_model(), tools=[], system_prompt=SYSTEM_PROMPT, callback_handler=None)


def _extract_json(raw_text: str) -> dict:
    text = raw_text.strip()
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            first_line, rest = text.split("\n", 1)
            text = rest if first_line.strip().lower() in ("", "json") else text
    match = _JSON_BLOCK_RE.search(text)
    if not match:
        raise ValueError("모델 응답에서 JSON을 찾지 못함")
    return json.loads(match.group(0))


@tool
def run(raw_requirement: str, analysis: dict, available_data: list[str]) -> dict:
    """데이터 선별 에이전트를 실행한다.

    이 함수는 실행 결과만 돌려주고, 그 결과가 다음 단계로 넘길만큼 괜찮은지는 판단하지 않는다
    — 그 판단(산출물 규격 검증, 재시도/롤백 여부)은 오케스트레이션의 몫이다.

    모델 호출 또는 응답 파싱이 실패하면 최대 MAX_ATTEMPTS(3)회까지 재시도한다.

    반환값: {"ok": bool, "data": dict | None, "error_message": str | None}
    이 함수는 예외를 던지지 않는다.
    """
    message = json.dumps(
        {
            "raw_requirement": raw_requirement,
            "analysis": analysis,
            "available_data": available_data,
        },
        ensure_ascii=False,
    )

    last_error: str | None = None

    for _attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            agent = build_agent()
            result = agent(message)
            parsed = _extract_json(str(result))
        except Exception as exc:  # noqa: BLE001 — 호출/파싱 실패를 규격화해서 반환
            last_error = str(exc)
            continue

        return {"ok": True, "data": parsed, "error_message": None}

    return {
        "ok": False,
        "data": None,
        "error_message": f"{MAX_ATTEMPTS}회 시도 후에도 실패: {last_error}",
    }
