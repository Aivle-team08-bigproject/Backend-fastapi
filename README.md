# Backend-fastapi

하나 데이터 마켓 백엔드다. FastAPI가 인증·직원 관리·데이터 요청·대시보드 API를
제공하고, Celery Worker가 요구사항 분석부터 데이터 가공까지의 비동기 파이프라인을
실행한다.

## 현재 구현 범위

파이프라인은 다음 세 단계를 순서대로 실행하며 각 단계가 끝날 때 사람의 승인(HITL)을
기다린다.

1. `REQUIREMENT_ANALYSIS` — 자연어 요구사항 구조화
2. `DATA_SELECTION` — 데이터셋·컬럼 선정과 합성 샘플 생성
3. `DATA_PROCESSING` — CSV 또는 익명화 DB 조회 결과 가공

상태 흐름은 다음과 같다.

```text
QUEUED
-> RUNNING (REQUIREMENT_ANALYSIS) -> WAITING_REQUIREMENT_REVIEW
-> RUNNING (DATA_SELECTION)       -> WAITING_SAMPLE_REVIEW
-> RUNNING (DATA_PROCESSING)      -> WAITING_FINAL_REVIEW
-> COMPLETED | FAILED
```

반려 시 실패 코드에 따라 되돌아갈 단계를 결정하고 새 attempt를 만든다. 시각화·보고서
생성, 최종 QA 자동화와 외부 오케스트레이터 연동은 아직 구현되지 않았다.

## 구성

`docker-compose.yml`이 실행하는 서비스는 세 개다. PostgreSQL은 NeonDB(관리형)를
사용하므로 Compose에 포함하지 않는다.

- `redis`: Celery broker/result backend 및 SSE 화면 갱신 채널
- `api`: FastAPI/Gunicorn
- `celery-worker`: Supervisor 및 단계별 에이전트 실행

별도의 상태 구독 서비스는 없다. Worker가 상태를 PostgreSQL에 먼저 기록한 뒤 Redis에
발행하고, FastAPI의 SSE 엔드포인트가 이를 구독한다. Redis 발행에 실패해도 DB 상태는
유지된다.

```text
API -> Redis broker -> Celery Worker
                         |-> PostgreSQL (상태·산출물 저장)
                         `-> Redis Pub/Sub -> FastAPI SSE -> Frontend
```

주요 디렉터리는 다음과 같다.

```text
Backend-fastapi/
├── agent_runtime/               # 단계별 에이전트와 검증된 데이터 조회 계층
├── app/
│   ├── domains/pipeline/        # 파이프라인 API, Supervisor, 산출물 검증
│   ├── worker/                  # Celery task, 상태 기록·화면 갱신 발행
│   └── domains/                 # 인증, 직원, 자동화, 대시보드 도메인
├── alembic/                     # service 스키마 마이그레이션
├── scripts/                     # 마이그레이션·데모 데이터 도구
├── tests/                       # API·도메인·파이프라인 테스트
├── examples.http                # API 호출 예시
└── docker-compose.yml
```

## 로컬 실행

### 1. Python 환경 준비

Python 3.12 기준이다.

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 환경변수 구성

저장소는 실제 비밀값이 담긴 `.env`를 추적하지 않으며 `.env.example`도 제공하지 않는다.
루트에 `.env`를 직접 만들고 최소한 아래 값을 설정한다. 세 DB URL은 NeonDB의 역할별
접속 정보로 채운다. NeonDB는 SSL을 강제하므로 `?sslmode=require`를 반드시 붙인다.

```dotenv
REDIS_PORT=6379
API_PORT=8000

PORTFOLIO_AGENT_DATABASE_URL=postgresql+psycopg://agent_svc:PASSWORD@<neon-host>/portfolio?sslmode=require
PORTFOLIO_APP_DATABASE_URL=postgresql+psycopg://app_svc:PASSWORD@<neon-host>/portfolio?sslmode=require
PORTFOLIO_MIGRATION_DATABASE_URL=postgresql+psycopg://portfolio_admin:PASSWORD@<neon-host>/portfolio?sslmode=require

JWT_SECRET=replace-with-a-long-random-secret
BOOTSTRAP_ADMIN_PASSWORD=replace-with-a-strong-password
ANON_HASH_SALT=replace-with-a-random-salt

DEEPSEEK_API_KEY=replace-with-your-api-key
REQUIREMENTS_ANALYSIS_MODEL_PROVIDER=deepseek
REQUIREMENTS_ANALYSIS_MODEL_ID=deepseek-v4-flash
DATA_SELECTION_MODEL_PROVIDER=deepseek
DATA_SELECTION_MODEL_ID=deepseek-v4-flash
```

앱의 전체 설정과 기본값은 `app/core/config.py`, 에이전트 모델 설정은
`agent_runtime/*/config.py`에서 확인할 수 있다. 비밀값이 든 `.env`는 커밋하지 않는다.

### 3. DB 준비

`mart`·`anon`·`service` 스키마와 역할·권한은 NeonDB에 이미 구성돼 있다. 별도의 로컬
PostgreSQL을 띄우지 않으며, `docker-compose.yml`도 DB 컨테이너를 포함하지 않는다.
2번에서 설정한 세 DB URL이 그대로 사용된다.

`service` 스키마 변경은 Alembic으로 관리한다.

```bash
alembic upgrade head
```

### 4. 서비스 실행

```bash
docker compose up -d --build
docker compose ps
```

Compose가 실행하는 서비스는 `redis`, `api`, `celery-worker` 세 개이며 DB는 NeonDB에
직접 접속한다.

확인 주소:

- Swagger UI: <http://127.0.0.1:8000/docs>
- Health check: <http://127.0.0.1:8000/health>

API와 Worker를 호스트에서 직접 실행하려면 Redis만 먼저 띄운다.

```bash
docker compose up -d redis
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
celery -A app.worker.celery_app:celery_app worker --loglevel=INFO --concurrency=2
```

두 프로세스는 각각 별도 터미널에서 실행한다.

## 파이프라인 사용

주요 API 흐름은 다음과 같다.

1. `POST /api/v1/data-requests` — 요청 생성 및 Celery 작업 등록
2. `GET /api/v1/runs/{run_id}` — 실행 상태와 단계별 결과 조회
3. `GET /api/v1/runs/{run_id}/sample-preview` — 최신 선별 단계의 합성 샘플 5건 조회
4. `POST /api/v1/runs/{run_id}/review` — 현재 단계 승인 또는 반려
5. `GET /api/v1/runs/{run_id}/events` — 진행 상태 SSE 구독

샘플 승인 후에는 `agent_svc` 계정으로 익명화 데이터베이스를 조회한다. 에이전트가 만든
SQL 문자열을 직접 실행하지 않고 등록된 데이터셋·컬럼·필터·조인만 SQLAlchemy 표현식으로
변환한다. 가공이 완료되면 `GET /api/v1/runs/{run_id}/result.csv`로 최종 결과를
다운로드할 수 있다. API와 Worker는 결과 파일을 위해 Compose의 `uploaded_data` 볼륨을
공유한다.

인증이 필요한 검토 API를 포함한 구체적인 요청 본문은 `examples.http`와 Swagger UI에서
확인한다.

## 에이전트와 Supervisor

`app/domains/pipeline/supervisor.py`는 다음 단계를 선택하고 입력 payload를 조립하며,
`app/domains/pipeline/validation.py`로 산출물 계약을 검증한다.

현재 연결된 런타임은 다음과 같다.

- `agent_runtime/requirements_analysis`: 요구사항 구조화
- `agent_runtime/data_selection`: 데이터셋·원본/파생 컬럼과 합성 샘플 설계
- `agent_runtime/query`: CSV 또는 익명화 DB의 검증된 조회 계층
- `agent_runtime/data_processing`: 결정론적 가공·익명화
- `agent_runtime/data_retrieval`: 허용 경로의 CSV 검증과 메타데이터 생성

요구사항 분석과 데이터 선별은 현재 DeepSeek의 OpenAI 호환 API를 사용한다.

## API 범위

현재 앱은 아래 영역을 제공한다.

- `/api/auth`: 로그인, 토큰 갱신, 로그아웃, 비밀번호 변경
- `/api/admin`: 직원·권한·세션 관리
- `/api/automation/requirements-analysis`: 단독 요구사항 분석
- `/api/v1`: 데이터 요청과 파이프라인
- `/api/dashboard`: 운영·개발자 대시보드

정확한 엔드포인트와 스키마는 실행 중인 Swagger UI를 기준으로 한다.

## 테스트

```bash
python -m pytest -q
```

주요 파이프라인 검증:

- `tests/test_supervisor_flow.py`: 단계 진행, 승인 게이트, 반려 롤백
- `tests/test_supervisor_stages.py`: 단계 선택과 검증 로직
- `tests/test_pipeline_dispatch.py`: Celery dispatch
- `tests/test_pipeline_status_events.py`: DB 상태 기록과 Redis/SSE 이벤트
- `tests/test_query_layer.py`: CSV·DB 조회 제한

테스트 설정은 `tests/conftest.py`가 외부 API 호출과 운영 인프라 의존성을 격리한다.
