from dataclasses import dataclass
import os


@dataclass(frozen=True)
class Settings:
    app_name: str = "Requirements Task Management API"
    api_prefix: str = os.getenv("API_PREFIX", "/api/v1")
    database_url: str = os.getenv("DATABASE_URL", "sqlite:///./requirements.db")


settings = Settings()
