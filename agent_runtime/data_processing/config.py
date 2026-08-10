from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


REPO_ROOT_ENV = Path(__file__).resolve().parents[2] / ".env"


class AgentSettings(BaseSettings):
    """가공 계획 LLM Agent의 독립 설정."""

    model_config = SettingsConfigDict(
        env_file=REPO_ROOT_ENV,
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    data_processing_model_provider: str = "deepseek"
    data_processing_model_id: str = "deepseek-v4-flash"
    agent_runtime_model_provider: str = "deepseek"
    agent_runtime_model_id: str = "global.anthropic.claude-haiku-4-5-20251001-v1:0"
    agent_runtime_region: str = "ap-northeast-2"
    data_processing_model_timeout_seconds: float = 60.0
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"


settings = AgentSettings()
