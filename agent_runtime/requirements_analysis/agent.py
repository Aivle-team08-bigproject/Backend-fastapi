"""요구사항 분석 에이전트 — 독립 실행 모듈.

사용자가 자연어로 보낸 가명화 데이터 요청을 구조화된 필드(사용목적/요구데이터 요약/카테고리/
전달매체/가공형태)로 분해한다. 이 에이전트 자신은 실제 데이터를 조회하지 않는다(툴 없음) —
"무엇을 요청받았는지"를 다음 단계(데이터 선별)가 바로 소비할 수 있는 구조화된 데이터로
분해하는 역할만 한다.

내부적으로 요청 분석 -> 요청 구조화 -> 데이터 범주화 3개의 의미적 단계를 독립된 프롬프트로
순차 실행한다(각 단계가 화면 체크리스트 표시 단위이자 독립된 재시도 경계). data_selection/
agent.py, data_processing/planning_agent.py와 동일한 패턴이다 — `on_step(step_code, status,
metadata)` 콜백 하나로 진행 상태를 알리고, 실제 DB 기록·Redis 발행은 호출자(app/ 쪽)가 한다.

이 모듈은 산출물이 다음 단계로 넘길만큼 괜찮은지 스스로 판단하지 않는다 — 모델을 호출하고
결과를 구조화해서 돌려주기만 한다. 그 결과가 규격에 맞는지, 통과하면 다음 단계로 보낼지,
실패하면 재시도/롤백할지는 이 에이전트의 책임이 아니다 — 파이프라인 전체를 조율하는
오케스트레이션(다른 담당자가 별도로 구현)이 이 함수의 반환값을 보고 판단한다.

이 모듈은 app/ 패키지(FastAPI)에 의존하지 않는다 — 나중에 AgentCore/Lambda로 별도 컨테이너
배포되거나, 다른 담당자가 만드는 오케스트레이션에 그대로 병합될 것을 염두에 두고, 이 폴더
(agent_runtime/requirements_analysis/)만으로 완결되게 짠다. `on_step` 콜백도 문자열
`step_code`/`status`만 주고받아서 app/ 쪽 enum(AnalysisStepCode 등)을 몰라도 된다.

`run()`은 @tool로 감싸져 있어 오케스트레이션 쪽 strands 에이전트가 표준 툴로 그대로 호출할 수
있다 — 입력 스키마가 함수 시그니처+docstring으로 자동 검증되기 때문에 병합 시 별도 어댑터가
필요 없다. `on_step` 콜백 인자는 `@tool` 스키마 생성에 영향을 주지 않도록 `run()`에는 넣지
않고, 콜백이 필요한 호출자는 `run_steps()`를 직접 쓴다.
"""

import json
import re
import time

from strands import Agent, tool
from agent_runtime.requirements_analysis.config import settings
from agent_runtime.model_factory import build_model

# 오케스트레이션이 산출물을 검증할 때 그대로 재사용할 수 있도록 공개해둔 허용값 —
# 이 프롬프트가 모델에게 지시하는 값과 검증 기준이 어긋나지 않으려면 이 상수를 참조해야 한다.
DELIVERY_CHANNELS = {"email", "api"}
OUTPUT_FORMATS = {"csv", "visualization", "report"}

# 화면 체크리스트 및 DB 기록에 쓰는 의미적 단계 코드. app/domains/pipeline/analysis_steps.py의
# AnalysisStepCode와 값이 동일해야 한다(그쪽도 이 파일을 import하지 않으므로 값을 맞춰서 중복 정의).
STEP_REQUEST_ANALYSIS = "REQUEST_ANALYSIS"
STEP_REQUEST_STRUCTURING = "REQUEST_STRUCTURING"
STEP_DATA_CATEGORIZATION = "DATA_CATEGORIZATION"

_COMMON_INTRO = """당신은 '하나 데이터 마켓'의 요구사항 분석 에이전트다.
이 서비스는 가명처리된 원본 데이터(회원정보/가맹점정보/결제내역 등)를 사용자의 자연어 요청에 맞춰
추출·가공해서 제공한다. 실제 데이터를 조회하거나 스키마를 알아낼 필요는 없다 — 그건 다음 단계
(데이터 선별)가 한다."""

REQUEST_ANALYSIS_PROMPT = f"""역할:
{_COMMON_INTRO}
지금은 3단계 중 1단계인 요청 분석을 담당한다. 원본 요청에서 사용 목적의 명시 여부와
필요 데이터의 의도를 사실대로 파악하며, 최종 구조화는 다음 단계에 맡긴다.
역할END.

규칙:
- usage_purpose_found에는 가공 데이터를 받은 뒤 실제로 쓸 활용 맥락만 적는다.
- "분석해줘/뽑아줘/추출해줘/제공해줘" 같은 요청 동사 자체는 사용 목적이 아니다.
- 실제 활용 맥락이 없으면 usage_purpose_found는 반드시 "명시되지 않음"으로 쓴다.
- data_intent에는 원본 요청에 없는 내용을 지어내지 않는다.
규칙END.

제약사항:
- 실제 데이터를 조회하거나 스키마를 추정하지 않는다.
- 다음 단계의 필드나 최종 전달 형식을 임의로 확정하지 않는다.
제약사항END.

출력형식:
JSON 하나만 출력한다. 부수적인 설명, Markdown, 코드 블록을 출력하지 않는다.
{{
  "usage_purpose_found": "요청에 명시된 사용 목적 또는 명시되지 않음",
  "data_intent": "원본 요청에 근거한 필요 데이터와 이유"
}}
출력형식END.

긍정 강화:
원본 요청에 충실한 분석은 다음 구조화와 데이터 선별의 정확도를 높입니다. 모든 필수 키를
점검한 뒤 JSON 하나만 반환하세요.
긍정 강화END."""

REQUEST_STRUCTURING_PROMPT = f"""역할:
{_COMMON_INTRO}
지금은 3단계 중 2단계인 요청 구조화를 담당한다. 1단계 분석 결과와 원본 요청을 대조해,
데이터 선별 Agent가 바로 소비할 수 있는 네 개의 필드로 정리한다.
역할END.

규칙:
- usage_purpose는 원본 요청에 명시된 실제 활용 맥락만 사용하며, 없으면 "명시되지 않음"이다.
- requested_data_summary에는 필요 데이터와 사용처를 한 문장으로 요약한다.
- delivery_channel이 명시되지 않으면 "api"를 사용한다.
- output_format이 명시되지 않으면 ["csv"]를 사용한다.
규칙END.

제약사항:
- delivery_channel은 {'|'.join(sorted(DELIVERY_CHANNELS))} 중 하나만 사용한다.
- output_format은 {'|'.join(sorted(OUTPUT_FORMATS))} 값만 담은 비어 있지 않은 배열이다.
- 원본 요청에 없는 목적, 데이터, 전달 방식을 지어내지 않는다.
제약사항END.

출력형식:
JSON 하나만 출력한다. 부수적인 설명, Markdown, 코드 블록을 출력하지 않는다.
{{
  "usage_purpose": "명시된 사용 목적 또는 명시되지 않음",
  "requested_data_summary": "필요 데이터와 사용처의 한 줄 요약",
  "delivery_channel": "api",
  "output_format": ["csv"]
}}
출력형식END.

긍정 강화:
허용값과 원본 요청을 모두 지킨 구조화 결과는 다음 데이터 선별 단계를 안정적으로 만듭니다.
모든 필수 키를 점검한 뒤 JSON 하나만 반환하세요.
긍정 강화END."""

DATA_CATEGORIZATION_PROMPT = f"""역할:
{_COMMON_INTRO}
지금은 3단계 중 3단계인 데이터 범주화를 담당한다. 원본 요청에 실제로 포함된 필터 조건만
분리하며, 분석 대상 자체를 필터로 해석하지 않는다.
역할END.

규칙:
- requested_data_categories에는 성별, 연령대, 지역, 업종처럼 요청에 명시된 필터 조건만 넣는다.
- 원본 요청에 없는 조건은 지어내지 않고, 언급되지 않은 카테고리는 키 자체를 넣지 않는다.
- 분석·추출 대상 자체는 필터 조건이 아니다.
- 키는 "성별", "연령대", "지역", "업종" 같은 한국어 명사로 쓴다.
- 여러 값은 ", "로 연결한 문자열 하나로 쓰되, "20대~30대" 같은 범위 표현은 원문 그대로 둔다.
규칙END.

제약사항:
- 배열, 영어 키, 중복 키를 만들지 않는다.
- 실제 데이터를 조회하거나 스키마를 추정하지 않는다.
제약사항END.

출력형식:
JSON 하나만 출력한다. 부수적인 설명, Markdown, 코드 블록을 출력하지 않는다.
{{"requested_data_categories": {{"카테고리명": "값"}}}}
출력형식END.

긍정 강화:
요청에 있는 조건만 정확히 분리하면 이후 데이터 선별이 안전하고 예측 가능해집니다. 형식을
점검한 뒤 JSON 하나만 반환하세요.
긍정 강화END."""

_JSON_BLOCK_RE = re.compile(r"\{.*\}", re.DOTALL)

# 각 단계 호출/검증이 실패했을 때(일시적 네트워크 오류, 형식 안 맞는 응답, 필수 키 누락 등)
# 그 단계 안에서 흡수할 수 있는 재시도 횟수. 이건 산출물 내용에 대한 판단이 아니라 실행
# 신뢰성 문제라 오케스트레이션이 아니라 여기서 처리한다.
MAX_ATTEMPTS = 3


def _build_model():
    return build_model(
        provider=settings.agent_runtime_model_provider,
        model_id=(
            settings.agent_runtime_model_id
            if settings.agent_runtime_model_provider.lower() == "bedrock"
            else settings.requirements_analysis_model_id
        ),
        deepseek_api_key=settings.deepseek_api_key,
        deepseek_base_url=settings.deepseek_base_url,
        region_name=settings.agent_runtime_region,
        temperature=0,
    )


def _build_agent(system_prompt: str) -> Agent:
    return Agent(model=_build_model(), tools=[], system_prompt=system_prompt, callback_handler=None)


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


def _missing_keys(data: dict, required: list[str]) -> list[str]:
    return [key for key in required if key not in data]


def _validate_request_analysis(parsed: dict) -> None:
    missing = _missing_keys(parsed, ["usage_purpose_found", "data_intent"])
    if missing:
        raise ValueError(f"모델 응답에 필수 키 누락: {', '.join(missing)}")


def _validate_request_structuring(parsed: dict) -> None:
    missing = _missing_keys(
        parsed, ["usage_purpose", "requested_data_summary", "delivery_channel", "output_format"]
    )
    if missing:
        raise ValueError(f"모델 응답에 필수 키 누락: {', '.join(missing)}")


def _validate_data_categorization(parsed: dict) -> None:
    missing = _missing_keys(parsed, ["requested_data_categories"])
    if missing:
        raise ValueError(f"모델 응답에 필수 키 누락: {', '.join(missing)}")


def _step_summary(step_code: str, result: dict) -> dict:
    """중간 산출물 대신 화면·운영에 필요한 작은 요약 지표만 반환한다."""
    if step_code == STEP_REQUEST_ANALYSIS:
        return {"usage_purpose_identified": result.get("usage_purpose_found") != "명시되지 않음"}
    if step_code == STEP_REQUEST_STRUCTURING:
        return {"output_format_count": len(result.get("output_format") or [])}
    return {"category_count": len(result.get("requested_data_categories") or {})}


def _run_prompt_step(
    *,
    system_prompt: str,
    user_message: str,
    validate,
    step_label: str,
    step_code: str,
    on_step=None,
    on_log=None,
) -> dict:
    if on_step is not None:
        on_step(step_code, "RUNNING", None)
    last_error: str | None = None
    message = user_message
    for attempt in range(1, MAX_ATTEMPTS + 1):
        started = time.monotonic()
        try:
            agent = _build_agent(system_prompt)
            raw = agent(message)
            raw_text = str(raw)
            elapsed_ms = int((time.monotonic() - started) * 1000)
            if on_log is not None:
                on_log(
                    "INFO",
                    f"{step_label} LLM 응답 수신 ({attempt}/{MAX_ATTEMPTS}회차, "
                    f"{elapsed_ms}ms, {len(raw_text)}자)",
                    {
                        "step": step_code,
                        "attempt": attempt,
                        "elapsed_ms": elapsed_ms,
                        "response_length": len(raw_text),
                    },
                )
            parsed = _extract_json(raw_text)
            validate(parsed)
        except Exception as exc:  # noqa: BLE001 - 검증 실패를 다음 시도의 피드백으로 흡수
            last_error = str(exc)
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
                    },
                )
            message = (
                f"{user_message}\n\n"
                f"[재시도 안내] 직전 {attempt}회차 {step_label} 결과가 다음 이유로 검증에 실패했습니다: "
                f"{last_error}. 위 요청은 그대로 두고, 지시된 JSON 형식만 다시 맞춰서 답하세요."
            )
        else:
            if on_step is not None:
                on_step(step_code, "COMPLETED", _step_summary(step_code, parsed))
            return parsed

    error_message = f"{step_label} 단계가 {MAX_ATTEMPTS}회 시도 후에도 실패: {last_error}"
    if on_step is not None:
        on_step(step_code, "FAILED", {"validation_errors": [last_error] if last_error else []})
    raise ValueError(error_message)


def run_steps(raw_request: str, on_step=None, on_log=None) -> dict:
    """요청 분석 -> 요청 구조화 -> 데이터 범주화를 독립 프롬프트로 순차 실행한다.

    `on_step(step_code, status, metadata)`가 주어지면 각 단계의 시작("RUNNING")·완료
    ("COMPLETED")·실패("FAILED") 시점에 호출한다. 어느 한 단계가 MAX_ATTEMPTS회를 전부
    시도해도 실패하면 예외를 던진다 — 호출자(agent_client.py)가 그대로 전파시켜서
    supervisor.py의 공통 예외 처리 경로(검증 실패 -> FAILED + failure_code)를 타게 한다.
    """
    step1 = _run_prompt_step(
        system_prompt=REQUEST_ANALYSIS_PROMPT,
        user_message=raw_request,
        validate=_validate_request_analysis,
        step_label="요청 분석",
        step_code=STEP_REQUEST_ANALYSIS,
        on_step=on_step,
        on_log=on_log,
    )

    structuring_input = (
        f"원본 요청: {raw_request}\n\n"
        f"1단계 분석 결과:\n"
        f"- 사용 목적: {step1.get('usage_purpose_found')}\n"
        f"- 데이터 필요 내용: {step1.get('data_intent')}"
    )
    step2 = _run_prompt_step(
        system_prompt=REQUEST_STRUCTURING_PROMPT,
        user_message=structuring_input,
        validate=_validate_request_structuring,
        step_label="요청 구조화",
        step_code=STEP_REQUEST_STRUCTURING,
        on_step=on_step,
        on_log=on_log,
    )

    step3 = _run_prompt_step(
        system_prompt=DATA_CATEGORIZATION_PROMPT,
        user_message=raw_request,
        validate=_validate_data_categorization,
        step_label="데이터 범주화",
        step_code=STEP_DATA_CATEGORIZATION,
        on_step=on_step,
        on_log=on_log,
    )

    return {
        "usage_purpose": step2["usage_purpose"],
        "requested_data_summary": step2["requested_data_summary"],
        "requested_data_categories": step3["requested_data_categories"],
        "delivery_channel": step2["delivery_channel"],
        "output_format": step2["output_format"],
    }


@tool
def run(raw_request: str) -> dict:
    """요구사항 분석 에이전트를 실행한다 (콜백 없이).

    strands 툴 스키마는 함수 시그니처로 자동 생성되므로, on_step 콜백 인자는 이 함수에
    넣지 않고 `run_steps()`로 분리했다 — 체크리스트 보고가 필요한 호출자는 그쪽을 직접 쓴다.

    반환값: {"ok": bool, "data": dict | None, "error_message": str | None}
    ok=False는 어느 한 단계가 MAX_ATTEMPTS회를 전부 시도해도 실패했다는 뜻이다(산출물 내용이
    아니라 실행 자체의 실패). 이 함수는 예외를 던지지 않는다.
    """
    try:
        final_result = run_steps(raw_request)
        return {"ok": True, "data": final_result, "error_message": None}
    except Exception as exc:  # noqa: BLE001 - tool 응답 계약은 실패를 JSON으로 반환
        return {"ok": False, "data": None, "error_message": str(exc)}
