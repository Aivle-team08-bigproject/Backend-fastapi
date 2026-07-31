from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """환경변수로 주입되는 앱 설정.

    ARCHITECTURE.md의 "core/ 앱 설정·부트스트랩 (환경변수로 모든 주소 주입)" 원칙에 따라
    이 파일 하나만 보면 어떤 환경변수가 필요한지 전부 알 수 있게 한다.
    """

    model_config = SettingsConfigDict(
        # 로컬 팀 환경에서 기존 `env` 파일과 표준 `.env` 파일을 모두 허용한다.
        # dotenv parser를 거치므로 JSON 값(CORS_ALLOWED_ORIGINS)과 CRLF도 안전하게 처리된다.
        env_file=("env", ".env"),
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # --- DB ---
    database_url: str = "postgresql+psycopg://appuser:change_me_strong_password@127.0.0.1:5432/appdb"

    # --- Celery / Redis 비동기 파이프라인 ---
    celery_broker_url: str = "redis://127.0.0.1:6379/0"
    celery_result_backend: str = "redis://127.0.0.1:6379/1"
    celery_task_always_eager: bool = False
    worker_status_redis_url: str = "redis://127.0.0.1:6379/2"
    # Worker가 DB에 상태를 쓴 뒤 프론트 화면 갱신용으로 발행하는 채널(FastAPI SSE가 구독)
    worker_status_sse_channel: str = "pipeline:run-status:persisted"
    worker_status_key_prefix: str = "pipeline:run-status:latest"
    worker_status_ttl_seconds: int = 86400
    upload_root: str = "/app/uploads"
    csv_upload_max_bytes: int = 50 * 1024 * 1024
    pipeline_query_source: str = "csv"

    # --- 개발자 대시보드 ---
    dashboard_usd_to_krw_rate: Decimal = Decimal("1330")
    dashboard_timezone: str = "Asia/Seoul"

    # --- JWT (Access Token) ---
    jwt_issuer: str = "portfolio-data-market"
    jwt_audience: str = "portfolio-operator-platform"
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
    cors_allowed_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:3000",
        "http://127.0.0.1:3000",
    ]

    # --- 최초 관리자 계정 부트스트랩 ---
    bootstrap_admin_id: str = "DEMO-ADMIN-001"
    bootstrap_admin_password: str
    bootstrap_admin_name: str = "최초 관리자"
    bootstrap_admin_department: str = "IT관리팀"
    bootstrap_admin_email: str = "admin@company.com"

    # --- 회원가입 ---
    # 회사 직원만 가입할 수 있도록 이메일 도메인을 제한한다. 콤마로 여러 도메인을 나열할 수 있고,
    # JSON 형식이 아니므로 .env에 배열 문법 없이 바로 적으면 된다. 값을 바꾼 뒤에는 재배포(재시작)만
    # 하면 되고 코드 수정은 필요 없다.
    allowed_email_domains: str = "company.com"

    def allowed_email_domain_list(self) -> list[str]:
        return [
            domain.strip().lower()
            for domain in self.allowed_email_domains.split(",")
            if domain.strip()
        ]

    # 자동화 파이프라인 에이전트(요구사항 분석 등)의 모델/API 설정은 여기 없다 —
    # agent_runtime/ 아래 각 에이전트가 자체 설정을 갖는다. FastAPI 앱은 에이전트를
    # "호출"만 하고 그 내부 설정(API 키 등)을 알 필요가 없어야 한다는 원칙 때문.

    # --- portfolio DB (agent_svc / app_svc / portfolio_admin 3계정) ---
    # agent_svc: anon 스키마 전체 + service 실행계층 테이블 (읽기/쓰기)
    portfolio_agent_database_url: str = "postgresql+psycopg://agent_svc:change_me@127.0.0.1:5432/portfolio"
    # app_svc: service 스키마 전체 (mart 접근권한 없음 — 화면설계상 불필요함이 확인되어 철회됨)
    portfolio_app_database_url: str = "postgresql+psycopg://app_svc:change_me@127.0.0.1:5432/portfolio"
    # portfolio_admin: Alembic 마이그레이션·익명화 배치 전용
    portfolio_migration_database_url: str = "postgresql+psycopg://portfolio_admin:change_me@127.0.0.1:5432/portfolio"

    # 익명화 배치에서 사용하는 가맹점 가명화 salt. 저장소에는 두지 않는다.
    anon_hash_salt: str = ""


settings = Settings()
