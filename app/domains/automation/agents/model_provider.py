"""자동화 파이프라인 단계별 LLM/SLM 모델 프로바이더 팩토리.

체크리스트대로 파이프라인 단계마다 쓰는 모델이 다르다(요구사항분석=LLM, 데이터선별=SLM 등).
지금은 요구사항 분석 단계 하나만 구현하지만, 나중에 provider를 바꾸거나(DeepSeek → 다른 API)
단계가 늘어나도 이 파일과 core/config.py의 관련 설정만 건드리면 되도록 분리해둔다.
"""

from strands.models.openai import OpenAIModel

from app.core.config import settings


def build_requirements_analysis_model() -> OpenAIModel:
    """요구사항 분석 단계 모델. 현재는 DeepSeek(OpenAI SDK 호환 API)를 사용한다.

    DeepSeek API가 OpenAI Chat Completions 스펙과 호환이라 Strands의 OpenAIModel에
    base_url만 바꿔서 그대로 쓴다. 나중에 다른 API로 교체할 때는 여기와 설정값만 바꾸면 된다.
    """
    return OpenAIModel(
        client_args={
            "api_key": settings.deepseek_api_key,
            "base_url": settings.deepseek_base_url,
        },
        model_id=settings.requirements_analysis_model_id,
    )
