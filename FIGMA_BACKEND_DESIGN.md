# Figma 기반 FastAPI 백엔드 설계

## 근거와 범위

분석 대상은 [3반 8조 빅프로젝트 Figma](https://www.figma.com/design/mZy2YAIhD3Zy5VtjVx3Yk6/3%EB%B0%98-8%EC%A1%B0-%EB%B9%85%ED%94%84%EB%A1%9C%EC%A0%9D%ED%8A%B8?node-id=0-1) 의 `Frame 7_FINAL_Pages`와 `Frame 7_FINAL_Dashboard`다. API로 확인한 화면은 다음 요구를 만든다.

- 8단계: 등록 → 요구사항 분석 실행 → 분석 검토 → 데이터 선별 실행 → 샘플 검토 → 데이터 가공 실행 → 최종 산출물 검토 → 완료/배포.
- 실행 화면은 단계별 진행률, 세부 이벤트, 에이전트 로그, 취소를 제공한다.
- 검토 화면은 승인, 자연어 피드백에 따른 재실행, 파일/API/이메일 산출물 제공을 제공한다.
- 운영자 화면은 전체/내 작업, 담당자·상태·기간 필터, 조치 대기(HITL) 목록을 제공한다.
- 개발자 화면은 토큰 사용량, 에이전트 상태·오류율·오류 로그, 직원 권한/활성 상태를 제공한다.

기존 `app/`의 인증·직원·요구사항/작업 API와 `automation-supervisor-api/`의 오케스트레이터를 새 서비스로 복제하지 않는다. 두 구현을 하나의 FastAPI 앱과 하나의 PostgreSQL 스키마로 합치고, UI의 `REQ-2024-0847`은 외부 노출용 요청 번호로 유지한다.

## 목표 구조

```text
Frontend
  ├─ REST: 등록·조회·승인·다운로드·대시보드
  └─ SSE: 작업 단계/진행률/에이전트 로그
FastAPI
  ├─ auth / employees                 기존 인증·권한 도메인
  ├─ requests                         요청과 8단계 상태 전이
  ├─ pipeline                         Supervisor, agent adapter, QA, rollback
  ├─ artifacts / delivery             파일, 보고서, API 배포, 이메일 발송
  └─ operations / observability       업무·운영·개발자 대시보드
PostgreSQL ── Redis Streams/PubSub ── Worker (Celery/ARQ 또는 AgentCore)
                     │
              SSE endpoint
Object storage (S3/MinIO): CSV, XLSX, 보고서, 첨부 파일
```

HTTP 요청에서 LLM·대용량 데이터 가공을 직접 실행하지 않는다. API는 `PipelineRun`을 생성해 큐에 넣고, worker가 단계 이벤트를 저장·발행한다. 프론트엔드는 `GET /events`를 연결하고 연결이 끊기면 `GET /runs/{id}`로 현재 상태를 복구한다.

## 상태 모델

화면의 8개 단계는 아래처럼 명시한다. `PipelineRun.status`는 현재 화면을, `StageRun.status`는 각 실행 이력을 나타낸다.

| 순서 | UI 단계 | stage_code | 진입/종료 |
| --- | --- | --- | --- |
| 1 | 요구사항 분석 가공 등록 | `INTAKE` | 요청 작성 → 제출 |
| 2 | 실시간 요구사항 분석 | `REQUIREMENT_ANALYSIS` | worker 실행 → `WAITING_REQUIREMENT_REVIEW` |
| 3 | 요구사항 완료 피드백 | `REQUIREMENT_REVIEW` | 승인 → 선별, 반려 → 분석 재실행 |
| 4 | 실시간 데이터 선별 | `DATA_SELECTION` | worker 실행 → `WAITING_SAMPLE_REVIEW` |
| 5 | 샘플데이터 및 피드백 | `SAMPLE_REVIEW` | 계약 승인 → 가공, 반려 → 선별 재실행 |
| 6 | 실시간 데이터 가공 | `DATA_PROCESSING` | worker 실행 → `WAITING_FINAL_REVIEW` |
| 7 | 최종 산출물 및 피드백 | `FINAL_REVIEW` | 승인 → 배포, 반려 → 가공 재실행 |
| 8 | 작업 완료 | `DELIVERY` | 파일/API/이메일 전달 기록 후 `COMPLETED` |

공통 terminal 상태는 `CANCELLED`, `FAILED`다. 재실행은 새 `stage_run`을 생성하고 `retry_of_id`로 이전 실행을 보존한다. 임의의 `PATCH status`는 제거한다. 모든 전이는 service 내부의 허용 전이표와 권한 검사로만 실행한다.

## API v1

### 요청·파이프라인

| 메서드/경로 | 권한 | 용도 |
| --- | --- | --- |
| `POST /api/v1/requests` | operator+ | 제목, 고객사, 자연어 요구사항, 산출물/전달 설정, 첨부를 등록하고 `request_no`를 반환 |
| `GET /api/v1/requests` | operator+ | 검색어, `assignee_id`, `status`, `stage`, `priority`, 날짜 범위, 페이지네이션 목록 |
| `GET /api/v1/requests/{request_no}` | 접근 가능 사용자 | 헤더, 진행 단계, 분석/샘플/최종 산출물 요약 |
| `POST /api/v1/requests/{request_no}/runs` | 담당자+ | 현재 허용 단계의 worker 실행 요청. `Idempotency-Key` 필수 |
| `GET /api/v1/requests/{request_no}/runs/{run_id}` | 접근 가능 사용자 | 단계별 상태, 진행률, metrics, 최신 로그 cursor |
| `GET /api/v1/requests/{request_no}/runs/{run_id}/events` | 접근 가능 사용자 | SSE (`stage_updated`, `progress`, `agent_log`, `artifact_ready`, `failed`) |
| `POST /api/v1/requests/{request_no}/runs/{run_id}/cancel` | 담당자 또는 관리자 | 취소 요청 및 worker cancellation token 발행 |
| `POST /api/v1/requests/{request_no}/reviews` | reviewer+ | `{review_type, decision, feedback, expected_changes}`. 승인/반려 전이를 원자적으로 수행 |
| `GET /api/v1/requests/{request_no}/reviews` | 접근 가능 사용자 | 검토와 피드백 이력 |

`POST /reviews`의 `review_type`은 `REQUIREMENT`, `SAMPLE`, `FINAL`, `decision`은 `APPROVED`, `CHANGES_REQUESTED`다. 피드백 반려 시 rollback 대상은 각각 `REQUIREMENT_ANALYSIS`, `DATA_SELECTION`, `DATA_PROCESSING`으로 고정한다. 화면의 “이전 단계”도 동일한 endpoint에 `decision=CHANGES_REQUESTED`와 명시된 `rollback_stage`를 보내며, 서버가 허용 범위를 검증한다.

### 산출물·전달

| 메서드/경로 | 용도 |
| --- | --- |
| `GET /api/v1/requests/{request_no}/artifacts` | CSV/XLSX/보고서/미리보기 metadata 목록 |
| `GET /api/v1/artifacts/{artifact_id}/download` | 짧은 만료의 object-storage signed URL 반환 |
| `GET /api/v1/artifacts/{artifact_id}/preview` | Top N 행 또는 보고서 요약. PII 원문은 반환 금지 |
| `POST /api/v1/requests/{request_no}/deliveries` | API 배포 등록 또는 허용된 수신자에게 이메일 발송 |
| `GET /api/v1/requests/{request_no}/deliveries` | 전달·다운로드·발송 감사 이력 |

API 배포 키의 원문은 DB에 저장하거나 재조회하지 않는다. 키는 Vault/Secrets Manager에 보관하고, DB에는 secret reference와 마지막 4자리만 기록한다.

### 대시보드·운영

| 메서드/경로 | UI |
| --- | --- |
| `GET /api/v1/dashboard/operations` | 전체 활성 작업, 단계별 건수/평균 소요, HITL 경고, 인기/보완 데이터, 작업 목록 |
| `GET /api/v1/dashboard/my-work` | 로그인 사용자의 긴급·마감 우선 작업과 월간 완료/품질 지표 |
| `GET /api/v1/dashboard/developer?period=day|week|month` | 토큰/비용 시계열, agent health, 오류율, 최근 오류 |
| `GET /api/v1/alerts` | 조치 대기·품질 임계치·SLA 위반 목록. 유형/상태 필터 |
| `POST /api/v1/alerts/{alert_id}/acknowledge` | 경고 담당자 지정 및 확인 기록 |

직원 관리와 로그인은 기존 `/auth`, `/employees` 도메인을 유지한다. 회원 목록에 필요한 필터는 `GET /employees?query=&role=&department=&active=`로 확장하고, 활성화/권한 변경은 기존 `require_permission` 의존성 아래에 둔다.

## 데이터 모델

데모 ERD의 `users`는 기존 `employees`로 통합한다. 기존 `requirements`/`tasks`는 별도 동기 legacy Base를 사용하므로, 새 비동기 서비스 DB에서는 `data_requests`를 요청 원장으로 사용한다. 기존 `Task`는 사람에게 배정되는 조치 항목이며, 파이프라인 실행 단위와 분리한다.

| 테이블 | 핵심 컬럼 | 목적 |
| --- | --- | --- |
| `data_requests` | `id`, `request_no`(unique), `customer_name`, `title`, `raw_requirement`, `assignee_id`, `current_status`, `current_stage`, `priority`, `due_at` | 화면의 작업 카드/상세 헤더 |
| `request_specs` | `request_id`, `purpose`, `categories JSONB`, `output_formats`, `delivery_channels`, `estimated_row_count`, `version` | AI 요구사항 분석의 승인 가능한 결과 |
| `pipeline_runs` | `id`, `request_id`, `status`, `current_stage`, `progress_percent`, `started_at`, `finished_at`, `cancel_requested_at` | 하나의 end-to-end 실행 |
| `stage_runs` | `id`, `pipeline_run_id`, `stage_code`, `status`, `attempt`, `input JSONB`, `output JSONB`, `quality_metrics JSONB`, `retry_of_id` | 재시도와 검증 가능한 단계 실행 |
| `pipeline_events` | `id`, `run_id`, `stage_run_id`, `event_type`, `severity`, `message`, `payload JSONB`, `occurred_at` | 화면 로그, SSE replay, 오류 대시보드 |
| `reviews` | `id`, `request_id`, `stage_run_id`, `review_type`, `decision`, `feedback`, `reviewer_id`, `created_at` | 승인·반려 감사 기록 |
| `artifacts` | `id`, `request_id`, `stage_run_id`, `kind`, `storage_key`, `mime_type`, `size_bytes`, `checksum`, `pii_scan_status` | 데이터/보고서/첨부 파일 |
| `deliveries` | `id`, `request_id`, `artifact_id`, `channel`, `recipient`, `secret_ref`, `status`, `sent_at` | 파일 다운로드·API 연동·메일 발송 |
| `agent_metrics` | `id`, `agent_name`, `model_name`, `run_id`, `input_tokens`, `output_tokens`, `cost_usd`, `latency_ms`, `outcome`, `recorded_at` | 개발자 대시보드 |
| `alerts` | `id`, `request_id`, `type`, `severity`, `status`, `assignee_id`, `detail JSONB`, `opened_at`, `resolved_at` | HITL/품질/SLA 조치 목록 |

데모 ERD의 `clients`, `contracts`, `api_keys`, `api_usage_log`, `pipeline_runs`, `pipeline_step_log`, `agent_execution_log`, `cost_tracking`, `artifacts`, `client_feedback`는 각각 `clients`, `contracts`, `contract_api_keys`, `api_usage_logs`, `pipeline_runs`, `stage_runs`, `pipeline_events`, `agent_metrics`, `artifacts`, `reviews`로 적용한다. API 키는 원문 대신 `key_hash`와 `key_last4`만 저장한다.

`pipeline_events(run_id, id)`, `stage_runs(pipeline_run_id, stage_code, attempt)`, `data_requests(current_status, current_stage, assignee_id, due_at)`, `agent_metrics(recorded_at, agent_name)`에 인덱스를 둔다. API 응답에서 내부 PK 대신 `request_no`와 UUID/opaque artifact ID를 사용한다.

## 구현 순서

1. `automation-supervisor-api`의 모델·service를 루트 `app/domains/pipeline/`로 이관하고 DB 세션을 async SQLAlchemy로 통일한다.
2. `data_requests`, `pipeline_runs`, `stage_runs`, `reviews`, `artifacts`, `deliveries`, `pipeline_events` Alembic migration과 상태 전이 단위 테스트를 추가한다.
3. 등록/조회/실행/검토 REST API와 기존 권한 의존성을 연결한다.
4. Redis 기반 event publisher와 SSE replay를 넣고, worker adapter를 stub→실제 agent 순서로 교체한다.
5. object storage signed URL, PII 검사 gate, 이메일/API delivery audit를 추가한다.
6. 운영·개발자 dashboard 집계 query를 materialized view 또는 일별 rollup으로 최적화한다.

### 수용 기준

- 등록된 요청은 8단계 중 정확히 하나의 현재 단계와, 모든 과거 stage run 이력을 가진다.
- 승인/반려/취소/재시도는 권한·허용 전이·감사 이벤트를 남기며 중복 요청에도 한 번만 처리된다.
- 진행 중 화면은 10초 polling 없이 SSE로 로그와 진행률을 수신하며, 재연결 뒤 누락 이벤트를 읽는다.
- 최종 산출물은 QA·PII 검사 `PASSED` 전에는 다운로드/배포/이메일 전달될 수 없다.
- 운영자와 개발자 대시보드는 원본 데이터가 아닌 집계·마스킹된 metadata만 반환한다.
