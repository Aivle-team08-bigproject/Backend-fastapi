from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """환경변수로 주입되는 앱 설정.

    ARCHITECTURE.md의 "core/ 앱 설정·부트스트랩 (환경변수로 모든 주소 주입)" 원칙에 따라
    이 파일 하나만 보면 어떤 환경변수가 필요한지 전부 알 수 있게 한다.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- DB ---
    database_url: str = "postgresql+psycopg://appuser:change_me_strong_password@127.0.0.1:5432/appdb"

    # requirements/tasks 도메인 전용 DB (동기 엔진, app/db/legacy_session.py에서 사용)
    requirements_database_url: str = "postgresql+psycopg://appuser:change_me_strong_password@127.0.0.1:5432/appdb"

    # --- JWT (Access Token) ---
    jwt_issuer: str = "hana-data-market"
    jwt_audience: str = "hana-operator-platform"
    jwt_secret: str
    access_token_ttl_minutes: int = 10

    # --- 세션 정책 ---
    # 유휴시간(idle timeout): 자리비움 상태에서 자동 로그아웃되는 기준 (국내 금융권 관행)
    session_idle_timeout_minutes: float = 10
    # 절대 타임아웃: 로그인 시각(createdAt) 기준, 계속 활동해도 이 시간이 지나면 무조건 종료
    session_normal_ttl_minutes: float = 30
    session_remember_me_ttl_hours: float = 8
    max_active_sessions: int = 3

    # 마지막 DB 갱신 이후 최소 몇 초가 지나야 다시 갱신할지
    session_activity_touch_interval_seconds: int = 30

    # --- Refresh Token 쿠키 ---
    refresh_cookie_name: str = "DM_REFRESH"
    cookie_secure: bool = False
    cookie_samesite: str = "lax"
    cookie_domain: str | None = None

    # --- CORS ---
    cors_allowed_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # --- 최초 관리자 계정 부트스트랩 ---
    bootstrap_admin_id: str = "HANA-ADMIN-001"
    bootstrap_admin_password: str
    bootstrap_admin_name: str = "최초 관리자"
    bootstrap_admin_department: str = "IT관리팀"

    # 자동화 파이프라인 에이전트(요구사항 분석 등)의 모델/API 설정은 여기 없다 —
    # agent_runtime/ 아래 각 에이전트가 자체 설정을 갖는다. FastAPI 앱은 에이전트를
    # "호출"만 하고 그 내부 설정(API 키 등)을 알 필요가 없어야 한다는 원칙 때문.

settings = Settings()
