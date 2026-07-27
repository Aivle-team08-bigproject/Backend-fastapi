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

    # # --- DB ---
    # database_url: str = "postgresql+psycopg://appuser:change_me_strong_password@127.0.0.1:5432/appdb"

    # # requirements/tasks 도메인 전용 DB (동기 엔진, app/db/legacy_session.py에서 사용)
    # requirements_database_url: str = "postgresql+psycopg://appuser:change_me_strong_password@127.0.0.1:5432/appdb"

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

    # --- hanacard DB (agent_svc / app_svc / hanacard_admin 3계정) ---
    # agent_svc: anon 스키마 전체 + service 스키마 중 실행계층 테이블만 (읽기/쓰기)
    hanacard_agent_database_url: str = "postgresql+psycopg://agent_svc:change_me@127.0.0.1:5432/hanacard"

    # app_svc: service 스키마 전체 (mart 접근권한 없음 — 화면설계상 불필요함이 확인되어 철회됨)
    hanacard_app_database_url: str = "postgresql+psycopg://app_svc:change_me@127.0.0.1:5432/hanacard"

    # hanacard_admin: Alembic 마이그레이션·익명화 배치 전용 (앱 런타임에는 사용하지 않음)
    hanacard_migration_database_url: str = "postgresql+psycopg://hanacard_admin:change_me@127.0.0.1:5432/hanacard"
    
    # 익명화 배치(scripts/anon_batch)의 가맹점 가명화 salt.
    # .env로만 주입하며 저장소에 두지 않는다 — 유출 시 토큰 역산 위험.
    anon_hash_salt: str = ""

settings = Settings()
