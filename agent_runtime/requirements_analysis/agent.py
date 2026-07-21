"""요구사항 분석 에이전트 — 독립 실행 모듈.

사용자가 자연어로 보낸 가명화 데이터 요청을 구조화된 필드(사용목적/요구데이터 요약/카테고리/
전달매체/가공형태)로 분해한다. 이 에이전트 자신은 실제 데이터를 조회하지 않는다(툴 없음) —
"무엇을 요청받았는지"를 다음 단계(데이터 선별)가 바로 소비할 수 있는 구조화된 데이터로
분해하는 역할만 한다.

이 모듈은 산출물이 다음 단계로 넘길만큼 괜찮은지 스스로 판단하지 않는다 — 모델을 호출하고
결과를 구조화해서 돌려주기만 한다. 그 결과가 규격에 맞는지, 통과하면 다음 단계로 보낼지,
실패하면 재시도/롤백할지는 이 에이전트의 책임이 아니다 — 파이프라인 전체를 조율하는
오케스트레이션(다른 담당자가 별도로 구현)이 이 함수의 반환값을 보고 판단한다. 이 모듈은
그 판단에 필요한 원재료(성공 여부 + 원본 데이터)만 정직하게 돌려주면 된다.

이 모듈은 app/ 패키지(FastAPI)에 의존하지 않는다 — 나중에 AgentCore/Lambda로 별도 컨테이너
배포되거나, 다른 담당자가 만드는 오케스트레이션에 그대로 병합될 것을 염두에 두고, 이 폴더
(agent_runtime/requirements_analysis/)만으로 완결되게 짠다.

`run()`은 @tool로 감싸져 있어 오케스트레이션 쪽 strands 에이전트가 표준 툴로 그대로 호출할 수
있다 — 입력 스키마가 함수 시그니처+docstring으로 자동 검증되기 때문에 병합 시 별도 어댑터가
필요 없다. `@tool`이 붙어도 이 파일 안에서든 다른 모듈에서든 그냥 파이썬 함수로 직접 호출
가능하다(콜러블 유지).
"""

import json
import re

from strands import Agent, tool
from strands.models.openai import OpenAIModel

from agent_runtime.requirements_analysis.config import settings

# 오케스트레이션이 산출물을 검증할 때 그대로 재사용할 수 있도록 공개해둔 허용값 —
# 이 프롬프트가 모델에게 지시하는 값과 검증 기준이 어긋나지 않으려면 이 상수를 참조해야 한다.
DELIVERY_CHANNELS = {"email", "api"}
OUTPUT_FORMATS = {"csv", "visualization", "report"}

SYSTEM_PROMPT = f"""당신은 '하나 데이터 마켓'의 요구사항 분석 에이전트다.
이 서비스는 가명처리된 원본 데이터(회원정보/가맹점정보/결제내역 등)를 사용자의 자연어 요청에 맞춰
추출·가공해서 제공한다.

당신의 임무는 사용자의 원본 자연어 요청 하나를 읽고, 아래 5개 항목으로 구조화하는 것이다.
실제 데이터를 조회하거나 스키마를 알아낼 필요는 없다 — 그건 다음 단계(데이터 선별)가 한다.

반드시 아래 JSON 형식으로만 응답한다. 다른 설명, 코드블록 마크다운(```), 서두 문구를 절대 붙이지 않는다:

{{
  "usage_purpose": "가공 데이터의 사용 용도. 예: 설문조사, 연구, 마케팅 분석 등. 요청에 명시 안 됐으면 '명시되지 않음'.",
  "requested_data_summary": "어떤 데이터가 필요하고 어디에 쓸지를 담은 자연어 한 줄 요약.",
  "requested_data_categories": {{"카테고리명": "값"}},
  "delivery_channel": "{'|'.join(sorted(DELIVERY_CHANNELS))} 중 하나. 명시 안 됐으면 'api'.",
  "output_format": ["{'|'.join(sorted(OUTPUT_FORMATS))} 중 하나 이상을 담은 배열. 명시 안 됐으면 [\\"csv\\"]."]
}}

규칙:
- requested_data_categories는 요청에서 실제로 언급된 조건만 담는다(예: 성별/연령대/지역/업종/
  결제성향 등) — 원본 요청에 없는 조건을 지어내지 않는다(환각 금지). 언급 안 된 카테고리는 아예
  키를 넣지 않는다.
- delivery_channel과 output_format은 반드시 위에 나열된 값만 쓴다. 다른 값을 임의로 만들지 않는다.
- JSON 외의 텍스트를 절대 출력하지 않는다."""

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)

# 모델 호출 또는 응답 파싱이 실패했을 때(일시적 네트워크 오류, 형식 안 맞는 응답 등) 이 안에서
# 흡수할 수 있는 재시도 횟수. 이건 산출물 내용에 대한 판단이 아니라 이 에이전트 자신의 실행
# 신뢰성 문제라 오케스트레이션이 아니라 여기서 처리한다 — 오케스트레이션의 RETRY_SAME_STAGE는
# 이 3회를 다 써도 실패했을 때만 발동한다.
MAX_ATTEMPTS = 3


def _build_model() -> OpenAIModel:
    """현재는 DeepSeek(OpenAI SDK 호환 API)를 사용한다.

    나중에 다른 API로 교체할 때는 이 함수와
    agent_runtime/requirements_analysis/config.py만 건드리면 된다.
    """
    return OpenAIModel(
        client_args={
            "api_key": settings.deepseek_api_key,
            "base_url": settings.deepseek_base_url,
        },
        model_id=settings.requirements_analysis_model_id,
    )


def build_agent() -> Agent:
    return Agent(model=_build_model(), tools=[], system_prompt=SYSTEM_PROMPT)


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


@tool
def run(raw_request: str) -> dict:
    """요구사항 분석 에이전트를 실행한다.

    이 함수는 실행 결과만 돌려주고, 그 결과가 다음 단계로 넘길만큼 괜찮은지는 판단하지 않는다
    — 그 판단(산출물 규격 검증, 재시도/롤백 여부)은 오케스트레이션(다른 담당자 구현)의 몫이다.

    모델 호출 또는 응답 파싱이 실패하면 최대 MAX_ATTEMPTS(3)회까지 재시도한다 — 일시적인
    네트워크 오류나 형식에 안 맞는 응답 한 번 때문에 오케스트레이션 단의 재시도(파이프라인
    재진입)까지 거슬러 올라가지 않도록, 이 안에서 흡수할 수 있는 실패는 이 안에서 흡수한다.

    반환값: {"ok": bool, "data": dict | None, "error_message": str | None}
    ok=False는 MAX_ATTEMPTS회를 전부 시도해도 모델 호출/응답 파싱이 실패했다는 뜻이다(산출물
    내용이 아니라 실행 자체의 실패). 이 함수는 예외를 던지지 않는다.
    """
    last_error: str | None = None

    for _attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            agent = build_agent()
            result = agent(raw_request)
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
