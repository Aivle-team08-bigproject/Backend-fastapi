"""FastAPI가 요구사항 분석 에이전트를 단독으로 호출/테스트하는 진입점.

주의: 오케스트레이션(파이프라인 전체를 조율하며 산출물을 검증·판단하는 로직)은 이 프로젝트의
범위가 아니다 — 다른 담당자가 별도로 구현하고, 나중에 각 단계 에이전트와 병합해서 통합
테스트한다. 여기서는 그 병합 전까지 이 에이전트 하나를 독립적으로 확인할 수 있도록, 에이전트의
원본 실행 결과를 그대로 노출한다 — 검증/판단을 흉내내지 않는다.

에이전트 자체의 구현(시스템 프롬프트, 모델 설정, strands Agent 객체)은 이 파일이 아니라
agent_runtime/requirements_analysis/에 있다. 지금은 같은 프로세스 안에서 함수를 직접 호출하는
로컬 구현이지만, 나중에 AgentCore/Lambda로 별도 배포하게 되면 invoke() 내부만 원격 호출로
바꾸면 되고, 이 모듈을 쓰는 서비스 계층은 손댈 필요가 없다.
"""

from agent_runtime.requirements_analysis.agent import run as _run
from agent_runtime.requirements_analysis.config import settings as _agent_settings

MODEL_PROVIDER = _agent_settings.requirements_analysis_model_provider
MODEL_ID = _agent_settings.requirements_analysis_model_id


def invoke(raw_request: str) -> dict:
    """요구사항 분석 에이전트를 실행한다 (원본 실행 결과 그대로 반환).

    반환값: {"ok": bool, "data": dict | None, "error_message": str | None}
    ok=False는 모델 호출/응답 파싱 자체의 실패를 뜻한다. 산출물 내용이 규격에 맞는지는
    이 함수도, 이 에이전트도 판단하지 않는다 — 그건 오케스트레이션(다른 담당자 구현)의 몫이다.
    이 함수는 예외를 던지지 않는다.
    """
    return _run(raw_request)
