from decimal import Decimal
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """환경변수로 주입되는 앱 설정."""

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
    # 이메일 요청 큐. 로컬은 Redis adapter, 운영은 SQS adapter로 교체한다.
    email_queue_enabled: bool = False
    email_queue_backend: str = "redis"  # local: redis, production: sqs
    email_request_queue_url: str = ""
    email_result_queue_url: str = ""
    aws_region: str = "ap-northeast-2"
    email_sqs_wait_seconds: int = 20
    email_sqs_visibility_timeout_seconds: int = 60
    email_stale_after_seconds: int = 900
    email_retry_poll_interval_seconds: int = 60
    email_request_queue_key: str = "email:delivery:requests"
    email_result_queue_key: str = "email:delivery:results"
    email_result_poll_interval_seconds: float = 1.0
    # 결과 worker가 DB 반영 직후 발행하는 채널(FastAPI SSE가 구독). pipeline
    # run-status 채널(worker_status_sse_channel)과는 이벤트 스키마가 달라 분리한다.
    email_delivery_sse_channel: str = "pipeline:email-delivery-status"
    upload_root: str = "/app/uploads"
    database_host_override: str | None = None
    # 최종 산출물 저장소. "local"이 기본값이라 S3 미설정 환경은 그대로 동작한다.
    # bigproject-infra/envs/dev가 만든 버킷을 쓰려면 s3로 바꾸고 bucket을 채운다.
    # 목표 아키텍처(AgentCore -> S3 direct write)가 아직 없어서 지금은 이 Celery
    # worker가 그 역할을 임시로 대신한다.
    artifact_storage_backend: Literal["local", "s3"] = "local"
    s3_artifacts_bucket: str = ""
    s3_artifacts_presign_expires_seconds: int = 300
    # 고객용 산출물 재다운로드 API를 서빙하는 Spring 서비스의 외부 base URL.
    # 실무자 화면에 보여줄 Endpoint URL을 여기서 조립한다.
    customer_api_base_url: str = "http://localhost:8082"
    # Spring(인터넷 노출)이 사내 민감 DB에 직접 붙지 못하게, 여기 이 내부 전용
    # 엔드포인트로 API 키 검증·조회를 대신 해준다. Spring의 InternalServiceInterceptor와
    # 같은 값을 공유해야 한다(양쪽 다 INTERNAL_SERVICE_KEY 환경변수).
    internal_service_key: str = "local-development-only-key"

    # --- 문서 텍스트 추출 (documents 도메인) ---
    # 원본 파일은 디스크에 저장하지 않고 메모리에서 바로 파싱 후 폐기한다(A안).
    document_upload_max_bytes: int = 10 * 1024 * 1024  # 10MB

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

    # --- 회원가입 ---
    # 회사 직원만 가입할 수 있도록 이메일 도메인을 제한한다. 콤마로 여러 도메인을 나열할 수 있고,
    # JSON 형식이 아니므로 .env에 배열 문법 없이 바로 적으면 된다. 값을 바꾼 뒤에는 재배포(재시작)만
    # 하면 되고 코드 수정은 필요 없다.
    allowed_email_domains: str = "company.com,example.com"

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
