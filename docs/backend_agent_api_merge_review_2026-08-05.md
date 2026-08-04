# Backend 에이전트·Front API 병합 검토안

## 1. 현재 상태

- 작업 브랜치: `feat/frontend-debt-backend-dashboard`
- 비교 대상: `origin/kimjounggun` (`f98d707 feat: harden derived processing pipeline`)
- `kimjounggun` 에이전트 구현과 현재 브랜치의 Front API 계약을 병합한다.
- `env_team`은 사용자 로컬 파일이므로 병합·커밋 대상에서 제외한다.

## 2. 병합 원칙

요구사항 분석, 데이터 선별, 데이터 가공 계획·실행과 관련된 Agent 구현은 `kimjounggun` 기준으로 채택한다.

`kimjounggun`의 가공 Agent는 다음 4단계로 분리되어 있다.

1. `DEDUPLICATION_PLAN`: 중복 제거 계획
2. `MISSING_VALUE_PLAN`: 결측 처리·형 변환 계획
3. `DERIVED_COLUMN_ORDER`: 파생 컬럼 생성 순서와 집계 계획
4. `FINAL_COLUMN_VALIDATION`: 최종 컬럼·품질 검증 정의

또한 `compare`, `logical`, `conditional`, `arithmetic`, `map_values` 연산, 파생 컬럼 의존성 정렬, 승인된 파생 컬럼 생성 여부 검증, 단계별 실패 snapshot을 제공한다.

현재 브랜치의 Agent 로깅(`log_agent_completion`, `agent_runtime/observability.py` 연동)은 채택하지 않는다.

## 3. 확정된 병합 방향

- 생성 요청 시 로그인 사용자를 담당자로 자동 지정한다.
- `PipelineRunResponse`에는 `requirement_analysis`, `error_message`, `failure`를 모두 포함한다.
- `GET /api/v1/runs/{run_id}/processing-result`를 유지한다.
- `GET /api/v1/runs/{run_id}/events`는 인증·`Last-Event-ID`·누락 이벤트 replay·snapshot을 포함한 `kimjounggun` 계약을 사용한다.
- `analysis_steps`, `selection_steps`, `processing_steps`는 단계 상태 응답과 DB 기록에 포함한다.
- `GET /api/v1/runs/{run_id}/sample-preview`와 `GET /api/v1/runs/{run_id}/events`는 서로 다른 화면을 위한 별도 계약으로 유지한다.
- 에이전트 구현과 에이전트 로깅은 `kimjounggun` 기준으로 병합한다.

## 4. 화면별 API 분리

| 화면 | API | 책임 |
|---|---|---|
| 샘플 데이터 검토 | `GET /api/v1/runs/{run_id}/sample-preview` | 합성 샘플 5건, 컬럼, 카탈로그 해석·이슈 반환 |
| 요구사항 분석·선별·가공 진행 모달 | `GET /api/v1/runs/{run_id}/events` | 현재 상태 snapshot, 단계별 진행 이벤트, 실패·재시도·롤백 상태 반환 |
| 최종 산출물 검토 | `GET /api/v1/runs/{run_id}/processing-result` | 실제 가공 결과·품질 리포트·보고서 반환 |

진행 모달은 `sample-preview`를 호출하지 않는다. SSE 최초 연결 시 `snapshot` 이벤트를 받고 이후 `status` 이벤트를 받으며, 연결이 끊기면 `Last-Event-ID`로 누락 이벤트를 재생한다.

## 5. API 비교와 병합 계약

| API/계약 | 현재 브랜치 | `kimjounggun` | 확정 방향 |
|---|---|---|---|
| `POST /api/v1/data-requests` | 로그인 사용자 인증 및 생성자 담당자 자동 지정 | 인증 의존성 없음 | 현재 브랜치 유지 |
| `GET /api/v1/runs/{run_id}` | `requirement_analysis` 포함 | `error_message`, `failure` 포함 | 세 필드 모두 포함 |
| `GET /api/v1/runs/{run_id}/sample-preview` | 합성 샘플·카탈로그 결과 반환 | 동일 계약 | 기존 응답 유지. 단계 상태는 추가하지 않음 |
| `GET /api/v1/runs/{run_id}/processing-result` | 존재. 최종 화면이 사용 | 없음 | 현재 브랜치 API 유지 |
| `GET /api/v1/runs/{run_id}/result.csv` | 존재 | 존재 | 양쪽 공통 유지 |
| `POST /api/v1/runs/{run_id}/review` | 승인·반려·`failure_code`·rollback 응답 | rollback 정책 보강 | Kim rollback 정책 + 기존 응답 필드 |
| `GET /api/v1/runs/{run_id}/events` | 인증 없음, 단순 상태/Redis 이벤트 | 인증, `Last-Event-ID` 재생, DB replay, snapshot | Kim 계약을 진행 모달 전용 API로 채택 |

## 6. Front가 기대하는 최종 결과 API

`FinalOutputFeedback`은 아래 API를 호출하므로 병합 후에도 반드시 유지한다.

```text
GET /api/v1/runs/{run_id}/processing-result
```

필수 응답 필드:

```text
api_result
processed_columns
quality_report
processing_explanation
visualization
report
processing_plan
```

완료 전에는 기존 `PIPELINE_RESULT_NOT_READY` 계약을 유지한다.

## 7. 단계 상태 계약

`kimjounggun` 병합 후 `PipelineRunResponse`와 SSE snapshot/event에는 다음 상태 정보를 포함한다.

```text
analysis_step / analysis_step_status
selection_step / selection_step_status
processing_step / processing_step_status
attempt_no
stage_run_id
step_metadata
failure
rollback_to_stage
```

기존 Front가 사용하는 `run_status`, `current_stage`, `progress_percent`, `stages`, `events`는 제거하지 않는다.

## 8. 타입 정규화 제외 결정

SQL 타입 정규화 도구는 이번 병합에서 제외한다. Agent 개발 책임과 Query Layer 책임이 중복되는 것을 막고, `kimjounggun`의 Agent 계약을 단일 기준으로 유지하기 위한 결정이다.

대신 Agent/선별 쿼리 계약이 DB 컬럼 타입에 맞는 값을 생성해야 한다. 특히 날짜 필터는 `timestamp with time zone`에 호환되는 timezone 포함 값으로 전달해야 하며, 이 계약을 지키지 않으면 `timestamp with time zone >= character varying` 오류가 다시 발생할 수 있다.

## 9. 병합 완료 기준

- 에이전트 구현은 `kimjounggun` 기준이다.
- Front 통신 API는 3개 화면 계약을 분리해 유지한다.
- Agent 로깅과 별도 타입 정규화 코드는 병합하지 않는다.
- `env_team`은 계속 커밋 대상에서 제외한다.
