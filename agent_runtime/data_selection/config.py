from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


REPO_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"


class AgentSettings(BaseSettings):
    """데이터 선별 에이전트 전용 설정.

    agent_runtime/requirements_analysis/config.py와 같은 이유로 FastAPI 앱 전역 설정과
    분리되어 있다 — 이 에이전트도 나중에 AgentCore/Lambda로 별도 배포될 독립 실행 단위라,
    이 파일 하나만 있으면 어떤 환경에서 돌리든 필요한 값을 전부 알 수 있어야 한다.

    DEEPSEEK_API_KEY/DEEPSEEK_BASE_URL은 requirements_analysis와 같은 값을 공유해서 쓴다
    (같은 .env에 이미 있는 값 그대로 재사용, 별도 키 발급 불필요).
    """

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT_ENV,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    data_selection_model_provider: str = "deepseek"
    data_selection_model_id: str = "deepseek-v4-flash"
    agent_runtime_model_provider: str = "deepseek"
    agent_runtime_model_id: str = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
    agent_runtime_region: str = "ap-northeast-2"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"


settings = AgentSettings()
