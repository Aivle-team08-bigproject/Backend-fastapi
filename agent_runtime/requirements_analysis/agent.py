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

from agent_runtime.observability import build_agent_completion_tool
from agent_runtime.requirements_analysis.config import settings

# 오케스트레이션이 산출물을 검증할 때 그대로 재사용할 수 있도록 공개해둔 허용값 —
# 이 프롬프트가 모델에게 지시하는 값과 검증 기준이 어긋나지 않으려면 이 상수를 참조해야 한다.
DELIVERY_CHANNELS = {"email", "api"}
OUTPUT_FORMATS = {"csv", "visualization", "report"}

SYSTEM_PROMPT = f"""역할:
당신은 하나 데이터 마켓의 요구사항 분석 담당자다.

사용자의 자연어 데이터 요청을 읽고 다음 세 단계를 순서대로 수행한다.

1. 요청 분석
   - 사용자가 무엇을 요청했는지 파악한다.
   - 데이터의 사용 목적과 필요한 데이터 내용을 확인한다.

2. 요청 구조화
   - 분석한 요청을 사용 목적, 데이터 요약, 전달 방식, 출력 형식으로 정리한다.
   - 다음 단계인 데이터 선별 에이전트가 바로 사용할 수 있도록 명확하게 작성한다.

3. 데이터 범주화
   - 요청에 실제로 포함된 필터 조건을 데이터 범주로 정리한다.
   - 예: 성별, 연령대, 지역, 업종 등
   - 사용자가 분석하려는 대상 자체는 필터 조건이 아니므로 데이터 범주에 넣지 않는다.

실제 데이터를 조회하거나 데이터베이스 구조를 확인하지 않는다.
실제 컬럼을 선택하거나 파생 컬럼을 설계하지 않는다.
역할 END.

추가 규칙:
- 요청에 명시되지 않은 사용 목적, 필터 조건, 데이터 범위를 임의로 만들지 않는다.
- 사용 목적은 데이터를 받은 뒤 실제로 어떻게 활용하는지에 대한 내용만 작성한다.
- "분석", "추출", "제공"과 같은 요청 동사만으로는 사용 목적을 판단하지 않는다.
- 사용 목적이 명시되지 않았다면 "명시되지 않음"으로 작성한다.
- 데이터 범주는 요청에 실제로 언급된 필터 조건만 포함한다.
- 분석 대상 자체는 데이터 범주가 아니라 데이터 요약에 포함한다.
- 데이터 범주의 이름은 "성별", "연령대", "지역", "업종"처럼 이해하기 쉬운 한국어 명사로 작성한다.
- 한 범주에 여러 값이 있으면 쉼표로 구분한 문자열 하나로 작성한다. 예: "여행, 숙박"
- "20대~30대"처럼 범위를 나타내는 표현은 원문 그대로 유지한다.
- 전달 방식은 "api" 또는 "email" 중 하나만 사용한다.
- 출력 형식은 "csv", "visualization", "report" 중에서만 선택한다.
- 실제 데이터 조회, 데이터베이스 확인, 컬럼 선별, 파생 컬럼 설계는 수행하지 않는다.
추가 규칙 END.

추가 지시:
- 먼저 최종 JSON을 완성한다.
- 최종 JSON의 필수 항목과 값이 규칙에 맞는지 확인한다.
- 확인이 끝나면 `log_agent_completion`을 정확히 한 번 호출한다.
- completed_tasks에는 반드시 ["요청 분석", "요청 구조화", "데이터 범주화"]를 같은 순서로 전달한다.
- 단계명을 바꾸거나 합치거나 추가하지 않는다.
- summary에는 요구사항 분석 결과를 한 문장으로 요약한다.
- 로깅 도구가 실패해도 최종 JSON 작성은 실패로 처리하지 않는다.
- 로깅 도구 호출과 도구 결과는 최종 답변이 아니다.
- 도구 결과를 받은 뒤 동일한 최종 JSON을 반환한다.
- 도구 호출만 남기고 답변을 끝내지 않는다.
추가 지시 END.

필수 제약사항:
- 최종 답변은 아래 JSON 하나만 반환한다.
- JSON 앞뒤에 설명, 마크다운, 코드블록, 서두 문장을 추가하지 않는다.
- 필수 키를 빠뜨리지 않는다.
- JSON 값에 요청에 없는 내용을 추측해서 넣지 않는다.
- requested_data_categories에는 실제 필터 조건만 넣는다.
- 필터 조건이 없으면 requested_data_categories는 빈 객체로 작성한다.
- 여러 범주의 값을 하나의 범주에 배열로 넣지 않고 쉼표로 구분한 문자열로 작성한다.
- delivery_channel은 "{'|'.join(sorted(DELIVERY_CHANNELS))}" 중 하나만 사용한다.
- output_format은 "{'|'.join(sorted(OUTPUT_FORMATS))}" 중 하나 이상을 배열에 넣는다.
- 출력 형식:

{{
  "usage_purpose": "데이터 사용 목적",
  "requested_data_summary": "필요한 데이터를 요약한 한 문장",
  "requested_data_categories": {{"범주명": "요청에 명시된 조건"}},
  "delivery_channel": "api",
  "output_format": ["csv"]
}}
필수 제약사항: END."""

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
        params={"temperature": 0},
    )


def build_agent() -> Agent:
    return Agent(
        model=_build_model(),
        tools=[build_agent_completion_tool("requirement-analysis-agent")],
        system_prompt=SYSTEM_PROMPT,
        callback_handler=None,
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
