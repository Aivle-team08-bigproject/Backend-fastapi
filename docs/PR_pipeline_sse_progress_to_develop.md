# PR 문서: `feature/pipeline-sse-progress` → `develop`

## 1. 변경 목적

파이프라인 진행 화면에서 에이전트 로그와 데이터 가공 체크리스트가 실제 실행 상태를 즉시
반영하도록 한다. 특히 AgentRuntimeClient의 로그 콜백으로 발생하던 이벤트 루프 교착 상태를
제거하고, 데이터 가공의 실행 후반 단계를 체크리스트에 연결한다.

## 2. `develop` 대비 주요 변경

### 에이전트 로그와 실행 교착 상태 수정

- `AgentRuntimeClient`의 동기 로그 콜백을 별도 스레드에서 실행한다.
  - Worker 이벤트 루프가 자기 자신에게 예약한 코루틴 완료를 기다리며 멈추는 문제를 방지한다.
- DB 메타데이터 조회, 원천 데이터 조회, 결정론적 가공 실행, 최종 계약 검증 실패를 로그로 남긴다.

### 데이터 가공 체크리스트 확장

기존 가공 계획 수립 4단계에 아래 실행 단계를 추가한다.

- 원천 데이터 조회
- 데이터 가공·품질 검증
- 최종 산출물 검증
- 결과 파일 생성

각 단계의 `RUNNING`·`COMPLETED`·`FAILED` 이벤트와 진행률을 backend에서 기록한다.

### 에이전트 프롬프트 계약 정리

요구사항 분석, 데이터 선별, 데이터 가공의 모든 시스템 프롬프트를 아래 구역으로 통일한다.

- 역할
- 규칙
- 제약사항
- 출력형식
- 긍정 강화

파생 컬럼 생성 순서 프롬프트에는 executor 계약을 명시했다. 예를 들어 `map_values.source`는
필수 operand 객체이고, `bucketize`의 입력 컬럼은 `source_columns`에만 둬야 한다.

## 3. 영향 범위

- `agent_runtime/requirements_analysis/agent.py`
- `agent_runtime/data_selection/agent.py`
- `agent_runtime/data_processing/planning_agent.py`
- `app/domains/pipeline/agent_client.py`
- `app/domains/pipeline/model.py`
- `app/domains/pipeline/processing_steps.py`
- `app/domains/pipeline/supervisor.py`

DB 스키마 변경이나 Alembic migration은 없다. Frontend의 단계 라벨·실패 단계 화면 이동은
별도 `Frontend_2` PR에서 반영한다.

## 4. 검증 체크리스트

- [x] `python -m compileall -q agent_runtime`
- [x] `tests/test_data_processing_plan.py`
- [x] `tests/test_data_selection_contract.py`
- [x] `tests/test_analysis_step_contract.py`
- [x] `tests/test_processing_step_contract.py`
- [x] `tests/test_agent_client.py`
- [x] 관련 계약 테스트 48개 통과
- [x] `backend-api`, `backend-worker` Docker 재빌드 및 기동 확인

## 5. 리뷰 포인트

- `run_coroutine_threadsafe(...).result()`를 사용하는 기존 콜백은 이벤트 루프에서 직접 호출하지
  않고 `asyncio.to_thread()`를 통해 호출하는지 확인한다.
- 파생 컬럼 `derivation_spec`과 processing executor operation 계약은 아직 별도 스키마다.
  이번 변경은 프롬프트에서 executor 형식 변환을 명시하지만, 장기적으로는 결정론적 변환기 또는
  단일 공통 계약으로 통합할지 검토가 필요하다.

## 6. 배포 시 유의사항

`backend-api`와 `backend-worker`는 동일한 에이전트 runtime 코드를 포함하므로 함께 배포해야
한다. 실행 중인 Celery 작업은 Worker 재시작 시 중단될 수 있으므로, 운영 배포 시 작업 큐 상태를
확인한다.
