"""요구사항 분석 에이전트.

사용자가 자연어로 보낸 가명화 데이터 요청을, 이후 파이프라인 단계(데이터 선정 / 데이터 가공 / QA)의
에이전트가 그대로 시스템 프롬프트로 쓸 수 있는 지시문으로 번역한다. 이 에이전트 자신은 실제 데이터를
조회하지 않는다(툴 없음) — "무엇을 요청받았는지"를 다음 단계가 바로 실행할 수 있는 지시문으로
분해하는 역할만 한다. 사람이 나중에 원본 raw_request와 여기서 생성된 프롬프트를 나란히 비교해서
의도가 잘 반영됐는지 검수하므로, 프롬프트 안에 원본 요청의 조건을 빠짐없이 구체적으로 담아야 한다
— 애매하게 요약하면 다음 단계 에이전트가 임의로 해석해서 환각/누락이 생긴다.
"""

import json
import re

from strands import Agent

from app.domains.automation.agents.model_provider import build_requirements_analysis_model

SYSTEM_PROMPT = """당신은 '하나 데이터 마켓'의 요구사항 분석 에이전트다.
이 서비스는 가명처리된 원본 데이터(회원정보/가맹점정보/결제내역 등)를 사용자의 자연어 요청에 맞춰
추출·가공해서 제공한다. 전체 파이프라인은 아래 순서로 진행된다:
1. 데이터 가명화 (이미 완료된 상태로 가정)
2. 요구사항 분석 ← 지금 당신이 담당하는 단계
3. 데이터 선정 — SLM 에이전트가 가명화된 DB에서 조건에 맞는 데이터를 탐색·추출
4. 데이터 가공 — LLM 에이전트가 추출된 데이터를 포맷팅/시각화/보고서 형태로 가공
5. QA/검증 — 최종 결과물이 요구사항에 부합하는지, 환각(근거 없는 값)은 없는지 검증

당신의 임무는 사용자의 원본 자연어 요청 하나를 읽고, 3/4/5단계 각 에이전트가 그대로 시스템
프롬프트로 사용할 수 있는 지시문 3개를 작성하는 것이다. 실제 데이터를 조회하거나 스키마를 알아낼
필요는 없다 — 그건 각 하위 에이전트가 자기 단계에서 툴로 직접 한다. 당신은 오직 "무엇을, 어떤
조건으로, 어떤 형태로" 처리해야 하는지를 명확히 분해해서 다음 단계로 넘기는 역할만 한다.

반드시 아래 JSON 형식으로만 응답한다. 다른 설명, 코드블록 마크다운(```), 서두 문구를 절대 붙이지 않는다:

{
  "summary": "원본 요청을 한국어 1~2문장으로 재해석한 요약. 사람이 원본 요청과 나란히 비교해서 의도가 맞게 해석됐는지 검수하는 용도.",
  "data_selection_prompt": "3단계(데이터 선정) 에이전트에게 그대로 전달할 지시문. 대상 데이터(어떤 테이블/컬럼일 것으로 추정되는지), 필터 조건(연령대/기간/지역/업종 등 요청에 명시된 값), 조인이 필요한지, 집계가 필요한지를 최대한 구체적으로 포함한다. 요청에 없는 조건을 지어내지 말고, 모호한 부분은 '요청에 명시되지 않음 — 탐색 결과에 따라 판단'이라고 명시한다.",
  "data_processing_prompt": "4단계(데이터 가공) 에이전트에게 전달할 지시문. 필요한 가공 형태(단순 CSV/API 포맷팅, 시각화 차트 종류, 보고서 형태의 글 설명 필요 여부)를 요청에서 언급된 대로 명시한다. 요청에 시각화나 보고서 언급이 없으면 '기본 포맷팅만 필요, 시각화/보고서 불필요'라고 명시한다.",
  "qa_prompt": "5단계(QA) 에이전트에게 전달할 지시문. 최종 결과물이 만족해야 할 검증 기준(원본 요청의 필터 조건이 실제로 반영됐는지, 집계값에 근거 데이터가 있는지, 요청 범위를 벗어난 데이터가 섞이지 않았는지)을 체크리스트 형태로 명시한다."
}

주의사항:
- 원본 요청에 없는 조건을 절대 지어내지 않는다(환각 금지). 모호하면 모호하다고 명시할 것.
- 세 프롬프트는 서로 다른 에이전트에게 전달되므로, 각 프롬프트만 읽고도 그 단계가 무엇을 해야 하는지
  완결적으로 이해할 수 있어야 한다(다른 프롬프트를 참조하는 표현 금지).
- JSON 외의 텍스트를 절대 출력하지 않는다."""

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)
_REQUIRED_KEYS = {"summary", "data_selection_prompt", "data_processing_prompt", "qa_prompt"}


def build_agent() -> Agent:
    return Agent(
        model=build_requirements_analysis_model(),
        tools=[],
        system_prompt=SYSTEM_PROMPT,
    )


def _extract_json(raw_text: str) -> dict:
    text = raw_text.strip()
    # 모델이 지시를 어기고 코드블록으로 감싸는 경우까지 방어적으로 벗겨낸다.
    if text.startswith("```"):
        text = text.strip("`")
        if "\n" in text:
            first_line, rest = text.split("\n", 1)
            text = rest if first_line.strip().lower() in ("", "json") else text
    match = _JSON_BLOCK_RE.search(text)
    if not match:
        raise ValueError("모델 응답에서 JSON을 찾지 못함")
    return json.loads(match.group(0))


def analyze(raw_request: str) -> dict:
    """요구사항 분석을 실행하고 결과 dict를 반환한다.

    strands Agent 호출(`agent(...)`) 자체가 블로킹 동기 호출이라, 이 함수는 동기 함수로 두고
    서비스 계층에서 스레드풀로 감싸서 호출한다(`asyncio.to_thread`).

    반환값: {"summary", "data_selection_prompt", "data_processing_prompt", "qa_prompt"}
    모델 응답 파싱 실패 시 ValueError를 던진다 — 호출부에서 실행 기록을 FAILED로 남기는 데 사용.
    """
    agent = build_agent()
    result = agent(raw_request)
    parsed = _extract_json(str(result))

    missing = _REQUIRED_KEYS - parsed.keys()
    if missing:
        raise ValueError(f"모델 응답에 필수 키 누락: {missing}")

    return parsed
