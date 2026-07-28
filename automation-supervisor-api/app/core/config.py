from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    api_prefix: str = "/api/v1"
    # Supervisor 전용 계정. automation 스키마만 접근한다(V009).
    database_url: str = "postgresql+psycopg://supervisor_svc:change_me@127.0.0.1:5432/portfolio"
    redis_url: str = "redis://127.0.0.1:6379/0"
    max_qa_iterations: int = 3
    requirement_analysis_model: str = "sonnet-4.6"
    data_selection_model: str = "aws-nova"
    data_processing_model: str = "chatgpt-5.5"
    pipeline_query_source: str = "provided"
    portfolio_agent_database_url: str = (
        "postgresql+psycopg://agent_svc:change_me@127.0.0.1:5432/portfolio"
    )


settings = Settings()
