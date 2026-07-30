# Backend-fastapi

이 레포는 하나 데이터 마켓 백엔드 저장소다. 현재 브랜치는 인증·직원 관리와 `service` 스키마 기반의 데이터 요청·대시보드 API를 제공한다.

## 현재 구현 범위

현재 브랜치의 파이프라인 API는 DB 상태 계약을 검증하기 위한 하드코딩 실행기로 동작한다. 요청 생성 시 아래 단계의 완료 상태와 화면 snapshot을 `service` DB에 저장한다.

- `REQUIREMENT_ANALYSIS`
- `DATA_SELECTION`
- `DATA_PROCESSING`
- `HITL_REVIEW`

실제 에이전트 실행기와 외부 브로커 연결은 후속 작업 범위다.

## 빠른 실행

루트 FastAPI와 PostgreSQL을 함께 실행하는 순서는 다음과 같다.

```bash
cd /path/to/Backend-fastapi
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env                 # 비밀번호와 로컬 설정 확인

docker compose up -d db
docker compose ps                     # db가 healthy인지 확인
./sqlfiles/bootstrap.sh --with-v001 --create-roles

uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

확인 주소:

- Swagger UI: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- Health: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

## 파이프라인 실행 환경

현재 루트 FastAPI 앱은 `service` 스키마에 인증·요청·파이프라인 상태를 저장하고 조회한다.
파이프라인 단계는 현재 하드코딩 실행기로 완료 처리한다.

```bash
docker compose up -d db
```

요청 생성 직후 `pipeline_runs`/`stage_runs`/`pipeline_events`와 작업 화면 snapshot이 저장된다.

기존 PostgreSQL volume을 재사용하는 경우 모델에서 삭제한 `stage_runs.executor`와
`stage_runs.executor_reference` 컬럼이 물리적으로 남을 수 있다. 먼저 점검한 뒤 명시적으로
삭제한다.

```bash
python -m scripts.migrate_remove_executor_columns
python -m scripts.migrate_remove_executor_columns --apply
```

### DB 구축

`sqlfiles`는 PostgreSQL의 `mart`·`anon`·`service` 스키마를 초기화한다. `mart`와 `anon`의
구조·권한은 SQL migration이 만들고, FastAPI가 사용하는 `service` 21개 테이블은 Alembic이
생성한다. 새 환경에서는 애플리케이션 실행 전에 아래 명령을 한 번 실행한다.

```bash
cp .env.example .env                 # 역할 비밀번호·JWT_SECRET을 로컬 값으로 변경
docker compose up -d db
docker compose ps                     # db가 healthy인지 확인
./sqlfiles/bootstrap.sh --with-v001 --create-roles
```

`--with-v001`은 DB·스키마 기본 설정, `--create-roles`는 `agent_svc`, `app_svc`,
`hanacard_admin` 계정을 생성·보정한다. 기존 DB에 다시 실행할 때는 두 옵션을 생략하고,
소유권이 어긋난 경우에만 `sqlfiles/patch/P001__align_existing_db.sql`을 검토한다.

CSV 원천 데이터는 개인정보 보호를 위해 Git에 포함하지 않는다. 별도 권한 저장소에서
`sqlfiles/seed/*.csv`를 받은 환경에서만 선택적으로 적재·검증한다.

```bash
./sqlfiles/bootstrap.sh --with-seed --with-verify
```

DB 볼륨까지 삭제하고 처음부터 다시 구성하려면 다음 명령을 사용한다.

```bash
docker compose down -v
docker compose up -d db
./sqlfiles/bootstrap.sh --with-v001 --create-roles
```

## 테스트 방법

### 1. 자동 테스트

```bash
python -m pytest -q
```

Supervisor 흐름은 `tests/test_supervisor_flow.py`가 스텁 에이전트로 검증한다 — 외부 API
호출 없이 요청 생성부터 단계 진행, 승인 게이트, 반려 롤백까지 실제 DB와 API를 거친다.
단계 선택·검증 로직 단위 테스트는 `tests/test_supervisor_stages.py`에 있다.

### 2. API 수동 검증

서버를 띄운 뒤 아래 순서로 호출한다.

1. `POST /api/v1/data-requests` — 요청 생성. Supervisor가 첫 단계를 실행하고 승인 대기로 멈춘다
2. `GET /api/v1/runs/{run_id}` — 실행 상태와 단계별 산출물 확인
3. `POST /api/v1/runs/{run_id}/review` — 단계 산출물 승인/반려
4. `GET /api/v1/runs/{run_id}/events` — 진행 상황 SSE 구독

요청 예시는 `examples.http`를 보면 된다.

## 프로젝트 목표

루트 문서 기준 전체 목표는 아래 5단계 자동화 파이프라인이다.

1. 요구사항 분석
2. 데이터 선별
3. 데이터 가공
4. 시각화 및 보고서 2차 가공
5. 최종 산출물 QA

관련 문서:

- [프로세스_개요.md](/Users/joupark/bigproject/프로세스_개요.md)
- [aws_workflow_architecture.md](/Users/joupark/bigproject/aws_workflow_architecture.md)

## 디렉터리

```text
Backend-fastapi/
├── agent_runtime/               # 독립 배포를 염두에 둔 에이전트 런타임 모듈
├── app/
│   ├── domains/pipeline/        # 파이프라인 API + 감시 Supervisor·산출물 검증
│   ├── worker/                  # Celery task, 상태 기록(DB)·화면 갱신 발행
│   └── ...                      # 인증·직원·대시보드 API
├── alembic/                     # service 스키마 마이그레이션
├── scripts/                     # 테스트·데모 데이터 적재
├── tests/                       # API·모델 테스트
└── docker-compose.yml           # db / redis / api / celery-worker
```

## 에이전트 구현 현황

### 1. 감시 Supervisor

`app/domains/pipeline/supervisor.py`가 단계 진행을 결정하고 산출물을 검증한다.

- 다음에 실행할 단계 선택 (`stage_runs`의 PENDING 행을 claim)
- 앞 단계 산출물로 다음 단계 payload 조립
- 단계 에이전트 호출과 산출물 검증 (`app/domains/pipeline/validation.py`)
- 반려 시 `failure_code -> 되돌아갈 단계` 정책 적용

상태 쓰기는 Worker가 한다 — `app/worker/status_recorder.py`가 DB에 쓰고, 그 다음 프론트
화면 갱신용으로 Redis에 발행한다(FastAPI SSE가 구독). 발행이 실패해도 상태는 DB에 남는다.

각 단계는 끝날 때마다 사람 승인에서 멈춘다.

```text
QUEUED
-> RUNNING (REQUIREMENT_ANALYSIS) -> WAITING_REQUIREMENT_REVIEW
-> RUNNING (DATA_SELECTION)       -> WAITING_SAMPLE_REVIEW
-> RUNNING (DATA_PROCESSING)      -> WAITING_FINAL_REVIEW
-> COMPLETED | FAILED
```

반려하면 해당 단계부터 새 attempt의 `stage_runs` 행이 만들어지고(이전 행은 `ROLLED_BACK`),
그 단계부터 다시 진행한다.

### 2. 현재 연결된 에이전트

Supervisor가 아래 3개 에이전트를 순서대로 호출한다. 구현체는 모두 `agent_runtime/`에 있고
`app/domains/pipeline/agent_client.py`가 어댑터 역할을 한다.

- `requirement-analysis-agent`
- `data-selection-agent`
- `data-processing-agent`

데이터 조회는 별도 단계가 아니라 가공 단계 안에서 `agent_runtime/query`가 담당한다
(업로드된 CSV 또는 익명화 DB, `PIPELINE_QUERY_SOURCE`로 선택).

모델 설정은 각 에이전트가 자체 `config.py`에서 `.env`를 읽는다 — FastAPI 앱 설정과
분리돼 있다.

```text
REQUIREMENTS_ANALYSIS_MODEL_PROVIDER=deepseek
REQUIREMENTS_ANALYSIS_MODEL_ID=deepseek-v4-flash
DEEPSEEK_API_KEY=...
```

### 3. 독립 런타임 모듈

`agent_runtime/`은 나중에 AgentCore 또는 Lambda로 분리 배포할 것을 전제로 둔 실험/준비 코드다.

현재 확인되는 구현:

- `agent_runtime/requirements_analysis/agent.py`
  요구사항 자연어를 구조화된 JSON으로 변환한다
- `agent_runtime/data_selection/agent.py`
  어떤 테이블을 어떤 기준으로 선별할지와 검토용 합성 샘플을 만든다
- `agent_runtime/data_processing/`
  결측 보정·형식 변환·익명화를 수행하는 결정론적 가공기
- `agent_runtime/data_retrieval/agent.py`
  선별된 CSV를 검증하고 메타데이터만 반환한다(원본 행은 반환하지 않음)
- `agent_runtime/query/`
  CSV·익명화 DB에서 실제 행을 읽어오는 조회 레이어

이 모듈은 FastAPI 앱에 직접 의존하지 않도록 분리돼 있다. 각 에이전트는 자기 산출물이
다음 단계로 넘길 만한지 스스로 판단하지 않는다 — 그 판단은 Supervisor가 한다.

## 미구현 항목

시각화 및 보고서 2차 가공, 최종 QA 자동화, 운영 데이터 소스 및 외부 오케스트레이션 연동은 아직 구현되지 않았다.

## API 범위

루트 `app/`은 인증, 세션, 직원 관리, 데이터 요청·파이프라인, 대시보드 API를 제공한다.
기존 `/requirements`·`/tasks` 레거시 API는 제거되었으며, 현재 작업 흐름은 `/api/v1` API를 사용한다.
## Celery · Redis 비동기 파이프라인

`POST /api/v1/data-requests`는 `service.pipeline_runs`에 실행을 먼저 등록하고,
동일한 `celery_task_id`로 Redis broker에 `pipeline.process_run` 작업을 발행합니다.
Worker는 요구사항 분석과 데이터 선별 상태를 Redis Pub/Sub으로 전달합니다.
`worker-status-subscriber`는 이벤트를 DB의 `pipeline_runs`, `stage_runs`,
`pipeline_events`에 저장한 뒤 SSE 전용 채널로 재발행합니다.
각 실행의 최신 이벤트는 `pipeline:run-status:latest:{run_id}` 키에도 TTL과 함께
저장되므로 SSE 재연결 시 가장 최근 상태부터 받을 수 있습니다.

실시간 상태는 `GET /api/v1/runs/{run_id}/events`로 구독합니다. 프론트 구독
예제는 `docs/frontend/pipelineEvents.ts`에 있습니다.

DB migration 적용 후 서비스를 실행합니다.

```bash
alembic upgrade head
docker compose up -d --build db redis api celery-worker worker-status-subscriber
```

API와 Worker는 `uploaded_data:/app/uploads` named volume을 공유합니다.
`POST /api/v1/runs/{run_id}/input-csv`에 multipart `file`로 CSV를 업로드하면
원본을 실행별 경로에 저장하고 Celery가 실제 행을 가공합니다. 완료 결과는
`service.artifacts`에 기록되며 `GET /api/v1/runs/{run_id}/result.csv`로
다운로드할 수 있습니다.

## Query Layer

`agent_runtime/query`는 데이터 선별 Agent의 결과를 검증된 `SelectionPlan`으로
변환합니다. Agent가 만든 SQL 문자열은 실행하지 않으며, 등록된 논리 데이터셋,
허용 컬럼, 필터 연산자와 최대 조회 건수만 SQLAlchemy 표현식으로 변환합니다.

- `CsvQueryExecutor`: 업로드 CSV에 같은 컬럼·필터·건수 계획 적용
- `DatabaseQueryExecutor`: `agent_svc` 계정으로 `anon` 스키마만 조회
- 민감 원본 컬럼(`card_number_masked`, `ip_address`, 사업자번호 등)은 DB 조회 차단
- 다중 데이터셋은 등록된 FK 조인 경로만 허용

기본 `PIPELINE_QUERY_SOURCE=csv`에서는 기존처럼 CSV 업로드를 기다립니다.
`PIPELINE_QUERY_SOURCE=database`로 실행하면 CSV가 없는 요청도 데이터 선별 직후
`anon` 데이터베이스를 조회하여 가공 단계로 전달합니다.
