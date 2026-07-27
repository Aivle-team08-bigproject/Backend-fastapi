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
`portfolio_admin` 계정을 생성·보정한다. 기존 DB에 다시 실행할 때는 두 옵션을 생략하고,
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

현재 구현 기준 검증 방법은 아래 두 가지다.

### 1. Dry-run

DB 없이 `stub agent`로 전체 흐름과 롤백 정책을 확인한다.

```bash
cd /Users/joupark/bigproject/Backend-fastapi/automation-supervisor-api
python scripts/dry_run_supervisor.py
```

예시:

```bash
python scripts/dry_run_supervisor.py --sample travel
python scripts/dry_run_supervisor.py --sample cafe
python scripts/dry_run_supervisor.py --requirement "30대 남성의 헬스 업종 월별 결제 변화를 차트와 CSV로 제공해줘."
python scripts/dry_run_supervisor.py --sample travel --csv /absolute/path/to/selected.csv
python scripts/dry_run_supervisor.py --sample subscription --feedback "가공 컬럼과 보고서 형식이 맞지 않습니다."
```

### 2. API 수동 검증

서버를 띄운 뒤 아래 순서로 호출한다.

1. `POST /api/v1/supervisor/jobs`
2. `POST /api/v1/supervisor/jobs/{job_id}/run`
3. `POST /api/v1/supervisor/jobs/{job_id}/hitl-review`
4. `GET /api/v1/supervisor/jobs/{job_id}`

요청 예시는 `automation-supervisor-api/examples.http`를 보면 된다.

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
├── app/                         # 인증·직원·파이프라인·대시보드 API
├── alembic/                     # service 스키마 마이그레이션
├── scripts/                     # 테스트·데모 데이터 적재
├── tests/                       # API·모델 테스트
└── docker-compose.yml           # 루트 PostgreSQL 실행용
```

## 에이전트 구현 현황

### 1. Supervisor API

`automation-supervisor-api/app/application/supervisor_service.py`가 전체 흐름을 제어한다.

- 작업 생성
- 단계별 실행
- 산출물 검증
- 동일 입력 재실행 시 캐시 재사용
- HITL 승인/반려 처리
- 반려 시 `failure_code -> rollback_stage` 고정 정책 적용

상태 흐름은 대략 아래와 같다.

```text
QUEUED
-> RUNNING
-> REQUIREMENT_ANALYSIS
-> DATA_SELECTION
-> DATA_PROCESSING
-> WAITING_HITL
-> COMPLETED | WAITING_RETRY | FAILED
```

### 2. 현재 연결된 에이전트

`automation-supervisor-api` 기준으로 아래 3개 agent 이름을 사용한다.

- `requirement-analysis-agent`
- `data-selection-agent`
- `data-processing-agent`

모델명은 `.env`에서 단계별로 분리한다.

```text
REQUIREMENT_ANALYSIS_MODEL=sonnet-4.6
DATA_SELECTION_MODEL=aws-nova
DATA_PROCESSING_MODEL=chatgpt-5.5
```

로컬에서 Strands 환경이 준비되지 않았으면 `stub agent`로 fallback 되도록 구성돼 있다. 그래서 API 구조와 상태 전이는 실제로 먼저 검증할 수 있다.

### 3. 독립 런타임 모듈

`agent_runtime/`은 나중에 AgentCore 또는 Lambda로 분리 배포할 것을 전제로 둔 실험/준비 코드다.

현재 확인되는 구현:

- `agent_runtime/requirements_analysis/agent.py`
  요구사항 자연어를 구조화된 JSON으로 변환하는 독립 실행형 Strands 에이전트

이 모듈은 FastAPI 앱에 직접 의존하지 않도록 분리돼 있다.

## 미구현 항목

시각화 및 보고서 2차 가공, 최종 QA 자동화, 운영 데이터 소스 및 외부 오케스트레이션 연동은 아직 구현되지 않았다.

## API 범위

루트 `app/`은 인증, 세션, 직원 관리, 데이터 요청·파이프라인, 대시보드 API를 제공한다.
기존 `/requirements`·`/tasks` 레거시 API는 제거되었으며, 현재 작업 흐름은 `/api/v1` API를 사용한다.
