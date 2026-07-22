# Backend-fastapi

이 레포는 하나 데이터 마켓 백엔드 저장소다. 현재 브랜치의 중심 구현은 기존 인증·요구사항 API와 별도로, 멀티 에이전트 파이프라인의 백엔드 오케스트레이션 예제인 `automation-supervisor-api/`에 있다.

## 현재 구현 범위

현재 브랜치에서 실제로 구현돼 있는 핵심은 `automation-supervisor-api/`다. 이 모듈은 프로젝트의 5단계 파이프라인 중 아래 흐름을 FastAPI Supervisor 형태로 실행한다.

- `REQUIREMENT_ANALYSIS`
- `DATA_SELECTION`
- `DATA_PROCESSING`
- `HITL_REVIEW`

즉, 지금 기준 구현 내용은 "3개 에이전트 실행 + 산출물 검증 + 캐시 + 사람 승인(HITL) + 고정 롤백 정책"이다.

## 빠른 실행

작업 디렉터리:

```bash
cd /Users/joupark/bigproject/Backend-fastapi/automation-supervisor-api
```

환경 준비:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env
```

서버 실행:

```bash
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

확인 주소:

- Swagger UI: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- Health: [http://127.0.0.1:8000/health](http://127.0.0.1:8000/health)

주요 환경값:

- `DATABASE_URL`
- `MAX_QA_ITERATIONS`
- `REQUIREMENT_ANALYSIS_MODEL`
- `DATA_SELECTION_MODEL`
- `DATA_PROCESSING_MODEL`

## 로컬 비동기 실행 환경

루트 FastAPI 앱은 짧은 HTTP 요청만 처리하고, 향후 파이프라인 단계는 Celery worker에서
실행한다. 로컬에서는 Redis가 broker/result backend 역할을 하며, AWS 전환 시에는
`AgentRunner` 계약을 유지한 채 Celery 실행 adapter를 AgentCore adapter로 교체한다.

```bash
cd /Users/joupark/bigproject/Backend-fastapi
cp .env.example .env
docker compose up --build
```

worker 연결 확인:

```bash
docker compose exec worker celery -A app.pipeline.celery_app:celery_app inspect ping
```

현재 `pipeline.health_check` task는 환경 smoke test다. 실제 `execute_stage` task와
`pipeline_runs`/`stage_runs`/`pipeline_events` 저장은 설계 문서의 다음 구현 단계다.

### 데모 요구사항·원천 데이터 적재

`dummyData/`의 고객·카드·가맹점·MCC·거래 CSV와 `REQ-20260714-001`부터
`REQ-20260714-005`까지의 데모 요청을 서비스 DB에 적재한다. 각 요청은 아직
요구사항 분석 에이전트가 실행되지 않은 `WAITING_REQUIREMENT_REVIEW` 상태로 생성된다.

```bash
cd /Users/joupark/bigproject/Backend-fastapi
python -m scripts.seed_demo_data
```

이미 등록된 `source_datasets.dataset_code` 또는 `data_requests.request_no`는 건너뛰므로
명령을 다시 실행해도 중복 데이터가 생기지 않는다.

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
├── automation-supervisor-api/   # 현재 브랜치의 핵심 구현
├── agent_runtime/               # 독립 배포를 염두에 둔 에이전트 런타임 모듈
├── app/                         # 기존 인증/세션/요구사항 API
├── tests/                       # 기존 API 테스트
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

## 기존 루트 API

루트 `app/`은 기존 인증, 세션, 관리자, 요구사항/작업 관리 API를 담고 있다. 이쪽은 현재 브랜치의 핵심 에이전트 구현 대상은 아니지만, 기존 서비스 백엔드로 계속 남아 있다. 관련 내용은 [AUTH_MODULE.md](/Users/joupark/bigproject/Backend-fastapi/AUTH_MODULE.md)를 참고하면 된다.
