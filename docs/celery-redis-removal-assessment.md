# Celery·Redis 실행부 제거 판단 자료

작성 기준: `joungs` 브랜치 현재 상태

## 1. 현재 구현 수준

현재 브랜치에는 에이전트를 실제로 실행하는 worker 파이프라인이 완성되어 있지 않다. `POST /api/v1/data-requests`는 `data_requests`, `pipeline_runs`, `stage_runs`, 최초 `pipeline_events`를 DB에 저장하지만 Celery task를 enqueue하지 않는다.

| 영역 | 현재 상태 | 판단 |
| --- | --- | --- |
| FastAPI 요청 등록 | 구현됨 | 화면·DB 계약으로 보존 |
| Pipeline DB 모델 | 구현됨 | 실행 방식과 독립적이므로 보존 |
| Redis broker/result backend | 설정·Docker 구성만 존재 | 새 worker 설계 전 제거 가능 |
| Celery worker | `pipeline.health_check` smoke test만 존재 | 실제 업무 로직 없음 |
| 오케스트레이터/요구사항 분석 호출 | 별도 `automation-supervisor-api`와 runtime에 존재 | 루트 pipeline worker와 미연결 |
| AgentRunner | Protocol(계약)만 존재 | 새 실행 구조의 인터페이스 후보로 보존 |
| SSE | 미구현 | 제거 대상 아님, 이후 전달 방식으로 재설계 |

## 2. 제거 범위

### 즉시 삭제해도 되는 실행 인프라

- `app/pipeline/celery_app.py`
- `app/pipeline/tasks.py`
- `docker-compose.yml`의 `redis`, `worker` 서비스
- `app/core/config.py`의 `celery_broker_url`, `celery_result_backend`, `celery_task_always_eager`
- `requirements.txt`의 `celery[redis]`, `redis`
- `tests/test_pipeline_environment.py`

이 파일들은 현재 DB 상태 저장이나 화면 API를 직접 제공하지 않으며, 새 공동 작업 브랜치에서 worker 실행 방식을 다시 설계할 때 중복 기준이 될 수 있다.

### 참조를 정리해야 하는 파일

- `app/domains/pipeline/service.py`: `StageRun.executor="CELERY"`를 새 실행 방식에 맞게 `PENDING` 또는 일반화된 executor 값으로 변경
- `app/domains/pipeline/model.py`: `StageRun.executor` 컬럼의 `default="CELERY"`를 제거하거나 새 실행기 기본값으로 변경
- `scripts/seed_demo_data.py`: 데모 실행 이력의 `executor="CELERY"` 값 변경
- `README.md`, `BACKEND_ARCHITECTURE_OVERVIEW.md`, `PROJECT_COMPLETION_TODO.md`, `FRONTEND_BACKEND_INTEGRATION_STATUS.md`, `FIGMA_BACKEND_DESIGN.md`: Celery·Redis가 현재 구현된 것처럼 보이는 문구를 “재설계 예정”으로 수정
- `app/pipeline/contracts.py`, `app/pipeline/runners/base.py`: 삭제하지 말고 새 worker와 FastAPI 사이의 실행·이벤트 계약으로 재검토

## 3. 보존해야 하는 공통 기반

- `app/domains/pipeline/model.py`: `DataRequest`, `PipelineRun`, `StageRun`, `PipelineEvent`, `Artifact`, `AgentMetric` 모델
- `app/domains/pipeline/router.py`, `schema.py`, `service.py`: 요청 생성과 실행 상태 조회 API
- `app/domains/dashboard/`: DB 상태를 프론트 화면용 read model로 변환하는 코드
- `agent_runtime/`: 요구사항 분석·데이터 선별·데이터 가공 에이전트 구현
- `automation-supervisor-api/`: 오케스트레이터의 독립 실행 코드. 다만 루트 FastAPI와 결합 방식은 새 설계에서 결정
- `scripts/seed_demo_data.py`, `scripts/seed_dashboard_demo.py`: 화면과 API 검증에 필요한 DB 데모 데이터

## 4. 새로 시작할 때의 기준선

삭제 후 기준선은 **FastAPI가 요청과 상태를 DB에 기록하고, 실행기는 아직 연결하지 않은 상태**로 두는 것이 적절하다. 이후 공동 작업 브랜치에서는 다음 계약만 먼저 확정한다.

1. API가 생성하는 `PipelineRun`·`StageRun` 상태 전이
2. worker가 받아야 하는 실행 요청 payload
3. worker가 기록해야 하는 `PipelineEvent`·`Artifact`·`AgentMetric`
4. 실패·재시도·중복 실행 처리 규칙
5. 로컬 실행기와 AWS AgentCore 실행기를 교체할 adapter 경계

## 5. 결론

현재 삭제해도 기능 손실이 작은 범위는 Celery 앱, health task, Redis/worker Docker 서비스, Celery 설정·의존성·환경 테스트다. Pipeline DB 모델과 상태 조회 API까지 삭제하면 프론트의 작업 상태 화면과 새 worker가 사용할 기준 계약을 잃게 되므로 유지해야 한다. 단, 삭제 작업을 시작하기 전에 문서와 seed 데이터의 `CELERY` 문자열을 함께 정리해야 이전 실행 방식이 남아 보이지 않는다.

## 6. 기존 DB volume 정리

모델에서 제거한 `stage_runs.executor`, `stage_runs.executor_reference`는 `create_all()`이 기존 테이블의 컬럼을 삭제하지 않기 때문에 volume에 남을 수 있다. `scripts/migrate_remove_executor_columns.py`는 기본 실행에서 상태만 점검하고, `--apply`를 지정했을 때만 `ALTER TABLE ... DROP COLUMN IF EXISTS`를 트랜잭션으로 수행한다.
