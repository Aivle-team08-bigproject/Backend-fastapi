# Backend 에이전트·Front API 병합 검토안

## 1. 현재 상태

- 작업 브랜치: `feat/frontend-debt-backend-dashboard`
- 비교 대상: `origin/kimjounggun` (`f98d707 feat: harden derived processing pipeline`)
- 이번 시도에서는 자동 merge 충돌만 확인하고 병합을 중단했다. 아직 코드 병합 결과는 없다.
- `env_team`은 사용자 로컬 파일이므로 병합·커밋 대상에서 제외한다.

## 2. 병합 원칙

### 에이전트 영역

요구사항 분석, 데이터 선별, 데이터 가공 계획·실행과 관련된 Agent 구현은 `kimjounggun` 기준으로 채택한다.

`kimjounggun`의 가공 Agent는 다음 4단계로 분리되어 있다.

1. `DEDUPLICATION_PLAN`: 중복 제거 계획
2. `MISSING_VALUE_PLAN`: 결측 처리·형 변환 계획
3. `DERIVED_COLUMN_ORDER`: 파생 컬럼 생성 순서와 집계 계획
4. `FINAL_COLUMN_VALIDATION`: 최종 컬럼·품질 검증 정의

또한 `compare`, `logical`, `conditional`, `arithmetic`, `map_values` 연산, 파생 컬럼 의존성 정렬, 승인된 파생 컬럼 생성 여부 검증, 단계별 실패 snapshot을 제공한다.

현재 브랜치의 Agent 로깅(`log_agent_completion`, `agent_runtime/observability.py` 연동)은 채택하지 않는다.

### Front 통신 영역

아래 API는 현재 Front 구현과 `kimjounggun` 구현을 비교한 뒤 병합 전 확정이 필요한 계약이다.

| API/계약 | 현재 브랜치 | `kimjounggun` | 병합 검토안 |
|---|---|---|---|
| `POST /api/v1/data-requests` | 로그인 사용자 인증 및 생성자 담당자 자동 지정 | 인증 의존성 없음 | 현재 브랜치 유지 권장. 작업 담당자 보장을 위해 인증 필요 |
| `GET /api/v1/runs/{run_id}` | `requirement_analysis` 포함 | `error_message`, `failure` 포함 | 세 필드를 모두 유지. 기존 Front 분석 결과와 Kim 실패 계약을 동시에 지원 |
| `GET /api/v1/runs/{run_id}/sample-preview` | 합성 샘플·카탈로그 결과 반환 | 동일 계약 + 단계 상태는 SSE/상세 상태로 제공 | Kim 단계 상태를 추가하되 기존 응답 필드는 유지 |
| `GET /api/v1/runs/{run_id}/processing-result` | 존재. 최종 화면이 사용 | 없음 | 반드시 현재 브랜치 API 유지. Front 최종 산출물 화면의 필수 계약 |
| `GET /api/v1/runs/{run_id}/result.csv` | 존재 | 존재 | 양쪽 공통 유지 |
| `POST /api/v1/runs/{run_id}/review` | 승인·반려·`failure_code`·rollback 응답 | 승인·반려·`failure_code`·rollback 정책 보강 | Kim의 rollback 정책을 채택하되 현재 응답 필드 유지 |
| `GET /api/v1/runs/{run_id}/events` | 인증 없음, 단순 현재 상태/Redis 이벤트 | 인증 필요, `Last-Event-ID` 재생, DB 누락 이벤트 replay, step snapshot | SSE를 사용할 경우 Kim 계약 채택. 현재 Front가 polling/직접 조회를 우선하므로 당장 화면 계약은 `GET /runs/{run_id}`로 유지 |

## 3. Front가 기대하는 최종 API 계약

### 최종 결과 조회

`FinalOutputFeedback`은 아래 API를 호출하므로 병합 후에도 반드시 유지해야 한다.

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

### 단계 상태

`kimjounggun` 병합 후 `PipelineRunResponse`와 SSE snapshot/event에는 다음 상태 정보가 추가된다.

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

이는 기존 Front가 무시할 수 있는 확장 필드이며, 기존 `run_status`, `current_stage`, `progress_percent`, `stages`, `events`는 제거하지 않는다.

## 4. 현재 브랜치에서 보존할 비-Agent 변경

- `GET /api/v1/runs/{run_id}/processing-result`
- `PipelineRunResponse.requirement_analysis`
- 생성 요청 시 인증 사용자 기반 담당자 지정
- `agent_runtime/query/normalization.py`의 SQL 바인딩 타입 정규화
- Front가 사용하는 기존 CSV 다운로드·샘플 미리보기·검토 응답 필드

타입 정규화는 Agent 프롬프트가 아니라 Query Layer의 안전한 바인딩 기능이므로, Agent 영역을 Kim 기준으로 채택하더라도 별도 보존 여부를 결정해야 한다. 기존 timestamp 오류 회귀 방지를 위해 보존을 권장한다.

## 5. 승인 요청 사항

다음 항목을 확정한 뒤 수동 merge한다.

1. `POST /data-requests`의 인증·담당자 자동 지정: 현재 브랜치 유지 여부
2. `PipelineRunResponse`에 `requirement_analysis`, `error_message`, `failure`를 모두 포함할지
3. `processing-result` API를 현재 브랜치에서 유지할지
4. SSE는 `kimjounggun`의 인증·이벤트 재생 계약을 채택할지
5. `analysis_steps`, `selection_steps`, `processing_steps`를 DB/API 응답에 포함할지
6. SQL 타입 정규화 도구를 Agent 병합과 별개로 유지할지

현재 권장안은 1·2·3·4·5·6 모두 유지/채택하는 것이다. 단, Agent 구현 자체와 Agent 로깅은 `kimjounggun` 기준으로만 병합한다.
