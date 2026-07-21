from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    """요구사항 분석 에이전트 전용 설정.

    app/core/config.py의 FastAPI 앱 전역 설정과 의도적으로 분리되어 있다. 이 에이전트는
    나중에 AgentCore/Lambda로 별도 배포될 독립 실행 단위라, FastAPI 프로세스의 설정 클래스에
    의존하면 안 된다 — 이 파일 하나만 있으면 이 에이전트를 어떤 환경에서 돌리든 필요한 값을
    전부 알 수 있어야 한다.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    requirements_analysis_model_provider: str = "deepseek"
    requirements_analysis_model_id: str = "deepseek-v4-flash"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"


settings = AgentSettings()
