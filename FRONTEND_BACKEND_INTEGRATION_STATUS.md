# Backend–Frontend 연동 현황 및 브랜치 통합 가이드

> 기준일: 2026-07-23  
> Backend: `Backend-fastapi/joungs` (`a051ce7`)  
> Frontend: `Frontend_2/joungs` (`4522465`)

이 문서는 현재 `joungs` 브랜치에서 구현된 백엔드 구조, 프론트엔드와의 통신 방식, 화면별 구현 수준, 그리고 `juns`·`guns` 브랜치 작업을 합칠 때 주의할 내용을 공유하기 위한 문서다.

---

## 1. 현재 상태 한눈에 보기

현재 프론트와 백엔드는 다음 수직 흐름까지 실제로 연결되어 있다.

```text
로그인
  → 대시보드 DB 조회
  → 로그인 사용자의 담당 작업 조회
  → 새 요구사항 DB 등록
  → PipelineRun/StageRun/Event 생성
  → 프론트가 3초마다 실행 상태 조회
```

단, **요구사항 등록 이후 실제 Celery worker가 에이전트를 실행하는 부분은 아직 연결되지 않았다.** 현재 새 요청을 생성하면 DB에는 `QUEUED`/`PENDING` 상태와 최초 이벤트가 저장되지만, 그 상태에서 자동으로 다음 단계로 진행되지는 않는다.

| 영역 | 현재 수준 | 비고 |
|---|---|---|
| 로그인·현재 사용자 | 구현 | JWT Access Token + Refresh Cookie 백엔드 구현 |
| 전체 작업 대시보드 | 구현 | DB의 데모 작업 및 dashboard read model 조회 |
| 내 작업 현황 | 구현 | 로그인 사용자의 `employee_code`와 작업 담당자 코드 연결 |
| 작업 필터 | 구현 | 프론트 클라이언트 필터(상태·담당자·등록 월) |
| 새 요구사항 등록 | 부분 구현 | DB 생성은 실제, 등록 전 헤더와 요청자 기본값은 데모 |
| 실행 상태 화면 | 부분 구현 | 실제 DB polling, worker 실행은 미연결 |
| 단계별 상세 화면 | 데모 구현 | DB에 저장된 `task_view_snapshots`를 화면에 표시 |
| 피드백·승인 저장 | 미구현 | 버튼은 화면 이동만 수행하거나 비활성 상태 |
| 에이전트 runtime 코드 | 구현 | 요구사항 분석·데이터 선별·데이터 가공 및 Supervisor dry-run |
| Celery 에이전트 실행 | 미구현 | 현재 `pipeline.health_check` task만 존재 |
| AgentCore 실행 | 미구현 | `AgentRunner` 계약만 준비됨 |
| SSE 실시간 스트림 | 미구현 | 현재 `GET /api/v1/runs/{run_id}`를 3초 간격 polling |

---

## 2. 전체 통신 구조

```text
React Page
  └─ page별 data adapter
       └─ src/shared/api.ts
            ├─ Authorization: Bearer <access-token>
            ├─ credentials: include
            └─ HTTP JSON
                 ↓
FastAPI Router
  └─ Domain Service
       └─ SQLAlchemy Session
            ├─ Async service DB models
            └─ Legacy sync requirements/tasks models
```

### 프론트엔드 통신 규칙

- API 기준 주소는 `VITE_API_BASE_URL`을 사용한다.
- 값이 없으면 `http://localhost:8000`을 사용한다.
- 공통 호출 함수는 `Frontend_2/src/shared/api.ts`의 `request<T>()`다.
- Access Token은 `Authorization: Bearer ...` 헤더로 전달한다.
- Refresh Token 쿠키 전달을 위해 모든 요청에 `credentials: 'include'`를 사용한다.
- 백엔드 응답은 `snake_case`, 프론트 페이지 데이터는 `camelCase`이며 page별 data adapter에서 변환한다.
- 오류 응답은 우선 `body.detail.message`, 없으면 HTTP status 기반 공통 메시지로 변환한다.

### 현재 인증 처리의 한계

- 프론트 `ProtectedRoute`는 토큰의 **존재 여부만** 검사한다.
- Access Token 만료 시 `/api/auth/refresh`를 자동 호출하는 interceptor는 아직 없다.
- 401 응답 시 토큰 삭제·로그인 이동 처리도 공통화되지 않았다.
- 백엔드는 Refresh Token을 HttpOnly Cookie로 발급하지만 프론트가 refresh/logout API를 아직 사용하지 않는다.

---

## 3. 프론트가 현재 사용하는 API 엔드포인트

### 3.1 인증

| Method | Endpoint | 인증 | 프론트 사용 | 역할 |
|---|---|---:|---:|---|
| `POST` | `/api/auth/login` | 불필요 | 사용 | 직원 ID/비밀번호 로그인, Access Token 반환 |
| `GET` | `/api/auth/me` | 필요 | 사용 | GNB 사용자 이름·부서 조회 |
| `POST` | `/api/auth/refresh` | Refresh Cookie | 미사용 | Access Token 재발급 |
| `POST` | `/api/auth/logout` | 필요 | 미사용 | 현재 세션 로그아웃 |
| `POST` | `/api/auth/logout-all` | 필요 | 미사용 | 모든 세션 로그아웃 |
| `POST` | `/api/auth/change-password` | 필요 | 미사용 | 비밀번호 변경 |

현재 데모 계정은 다음 DB 데이터로 맞춰져 있다.

```text
employee_code: DEMO-001
name: 홍길동 책임
department: 데이터 운영팀
```

### 3.2 요구사항 생성 및 실행 상태

| Method | Endpoint | 인증 강제 | 프론트 사용 | 현재 동작 |
|---|---|---:|---:|---|
| `POST` | `/api/v1/data-requests` | 아니요 | 사용 | 요청·PipelineRun·StageRun·최초 Event를 DB에 생성 |
| `GET` | `/api/v1/runs/{run_id}` | 아니요 | 사용 | 실행·단계·이벤트 상태 조회 |

요구사항 생성 요청:

```json
{
  "raw_requirement": "강남구 외식업 소비 트렌드를 분석해 주세요.",
  "title": "강남구 외식업 소비 분석",
  "requester_name": "요청 기관명"
}
```

현재 프론트는 `raw_requirement`만 전송한다. 따라서 다음 기본값이 사용된다.

- `title`: 요구사항 첫 번째 줄의 앞 200자
- `requester_name`: `프론트엔드 데모 요청자`

생성 응답:

```json
{
  "request_no": "REQ-20260723-ABC123",
  "run_id": 1,
  "request_status": "QUEUED",
  "run_status": "QUEUED",
  "current_stage": "REQUIREMENT_ANALYSIS",
  "created_at": "2026-07-23T00:00:00"
}
```

프론트는 응답을 받으면 다음 주소로 이동한다.

```text
/tasks/{request_no}/runs/{run_id}/analyzing
```

이 화면은 `GET /api/v1/runs/{run_id}`를 3초마다 호출한다. 현재 SSE나 WebSocket은 사용하지 않는다.

### 3.3 실무자 대시보드

| Method | Endpoint | 인증 강제 | 프론트 화면 | 데이터 출처 |
|---|---|---:|---|---|
| `GET` | `/api/v1/dashboard` | 아니요 | 전체 작업 | `data_requests`, alerts, insights |
| `GET` | `/api/v1/dashboard/my-tasks` | 예 | 내 작업 현황 | 현재 직원 + 담당자 코드가 같은 `data_requests` |
| `GET` | `/api/v1/dashboard/task-lookup` | 아니요 | 상세 조치 목록 | `alert_code=REQUIREMENT_GUIDE` 작업 |

`my-tasks`의 담당자 연결은 표시 이름이 아니라 아래 코드로 수행한다.

```text
employees.employee_code
    = data_requests.analysis_condition.assignee_employee_code
```

기존 데이터 호환을 위해 담당자 코드가 없을 때만 `analysis_condition.assignee == employee.name` 비교를 fallback으로 사용한다.

### 3.4 개발자·관리자 대시보드

| Method | Endpoint | 인증 강제 | 프론트 화면 | 데이터 출처 |
|---|---|---:|---|---|
| `GET` | `/api/v1/dashboard/developer` | 아니요 | 개발자 대시보드 | `system_dashboard_snapshots` JSON |
| `GET` | `/api/v1/dashboard/members` | 아니요 | 구성원 관리 화면 | `employees`, permissions |

주의: 화면의 이름은 관리자/개발자 대시보드지만 위 두 조회 API에는 현재 인증·권한 dependency가 없다. 프론트 라우트도 Access Token 존재만 확인하므로 운영 전 권한 검사가 필요하다.

### 3.5 단계별 작업 화면

| Method | Endpoint | 인증 강제 | 역할 |
|---|---|---:|---|
| `GET` | `/api/v1/tasks/{request_no}/views/{view_code}` | 아니요 | 단계별 화면용 read model 반환 |

현재 지원하는 `view_code`는 다음과 같다.

| view_code | 프론트 화면 | 현재 데이터 수준 |
|---|---|---|
| `register` | 요구사항 등록 | 데모 placeholder 및 기존 요청 헤더 |
| `analysis` | 분석 진행 데모 | snapshot |
| `review` | 요구사항 분석 피드백 | snapshot |
| `selection` | 데이터 선별 진행 | snapshot |
| `sample-feedback` | 샘플 데이터 피드백 | snapshot |
| `processing` | 데이터 가공 진행 | snapshot |
| `final-feedback` | 최종 산출물 피드백 | snapshot |
| `complete` | 작업 완료 | snapshot |

응답 형식:

```json
{
  "request_no": "REQ-2024-0847",
  "request_title": "요청 제목",
  "view_code": "review",
  "payload": {}
}
```

`payload`는 화면마다 구조가 다른 JSON이다. 현재는 `scripts/seed_dashboard_demo.py`가 생성하며, 실제 에이전트 결과를 projection 하는 로직은 아직 없다.

---

## 4. 백엔드에는 있지만 프론트가 사용하지 않는 API

### 4.1 요구사항 분석 에이전트 단독 API

| Method | Endpoint | 인증 | 설명 |
|---|---|---:|---|
| `POST` | `/api/automation/requirements-analysis` | 필요 | 요구사항 분석 에이전트를 동기 호출하고 결과 저장 |
| `GET` | `/api/automation/requirements-analysis` | 필요 | 최근 분석 실행 목록 |
| `GET` | `/api/automation/requirements-analysis/{run_id}` | 필요 | 분석 실행 단건 조회 |

이 API는 `asyncio.to_thread()`에서 요구사항 분석 클라이언트를 직접 호출한다. `/api/v1/data-requests`로 시작하는 pipeline 실행과 아직 연결되어 있지 않다.

### 4.2 직원·세션 관리자 API

다음 API는 실제 permission dependency가 적용되어 있다.

- `GET /api/admin/employees`
- `GET /api/admin/employees/{employee_code}`
- `POST /api/admin/employees`
- `PUT /api/admin/employees/{employee_code}/permissions`
- `PATCH /api/admin/employees/{employee_code}/status`
- `POST /api/admin/employees/{employee_code}/reset-password`
- `GET /api/admin/employees/permissions/catalog`
- `GET /api/admin/employees/audit-logs`
- `GET /api/admin/employees/{employee_code}/sessions`
- `DELETE /api/admin/sessions/{session_id}`

현재 프론트 구성원 관리 화면은 이 CRUD API가 아니라 조회 전용 `/api/v1/dashboard/members`만 사용한다.

### 4.3 Legacy 요구사항·작업 API

다음 API는 기존 동기 SQLAlchemy 모델을 사용하며 현재 프론트와 연결되어 있지 않다.

- `/requirements`
- `/requirements/{requirement_id}`
- `/requirements/{requirement_id}/status`
- `/requirements/{requirement_id}/status-history`
- `/requirements/{requirement_id}/tasks`
- `/tasks/{task_id}`
- `/tasks/{task_id}/status`
- `/tasks/{task_id}/status-history`

신규 프론트 흐름은 `data_requests`와 `/api/v1/...` API를 사용한다. 두 요구사항 모델을 바로 섞으면 ID·상태·세션 방식이 달라지므로 통합 방향을 먼저 결정해야 한다.

### 4.4 별도 Supervisor 애플리케이션

`automation-supervisor-api/`는 루트 FastAPI와 별도 애플리케이션이다. 주요 API는 다음과 같다.

- `POST /api/v1/supervisor/jobs`
- `POST /api/v1/supervisor/jobs/{job_id}/run`
- `POST /api/v1/supervisor/jobs/{job_id}/hitl-review`
- `GET /api/v1/supervisor/jobs/{job_id}`

이 API들은 현재 루트 `app/main.py`에 mount/include되어 있지 않고 프론트도 호출하지 않는다. Supervisor의 단계 실행 로직을 루트 `pipeline_runs/stage_runs/pipeline_events` 계약에 맞춰 합칠 작업이 필요하다.

---

## 5. 데이터베이스 구조

### 5.1 Async service DB

`app/db/session.py`의 async SQLAlchemy engine을 사용한다.

주요 테이블 그룹:

- 인증/직원: `employees`, `employee_permissions`, `login_sessions`, `admin_audit_logs`
- 단독 에이전트: `requirements_analysis_runs`
- 원천 데이터: `source_datasets`, `source_customers`, `source_cards`, `source_merchants`, `source_mcc_codes`, `source_transactions`
- 서비스 요청: `clients`, `data_requests`
- 실행: `pipeline_runs`, `stage_runs`, `pipeline_events`, `agent_metrics`
- 산출물/HITL: `artifacts`, `reviews`, `deliveries`
- 계약/API: `contracts`, `contract_api_keys`, `api_usage_logs`
- 프론트 조회 모델: `task_view_snapshots`, `dashboard_alerts`, `dashboard_insights`, `system_dashboard_snapshots`

### 5.2 Legacy requirements DB session

`app/db/legacy_session.py`는 별도의 동기 engine과 별도 SQLAlchemy Base를 사용한다.

- `requirements`
- `requirement_status_history`
- `tasks`
- `task_status_history`

환경에 따라 두 engine이 같은 PostgreSQL database URL을 가리킬 수는 있지만, ORM metadata와 트랜잭션 경계는 서로 다르다.

### 5.3 현재 스키마 관리 방식

- 앱 시작 시 `Base.metadata.create_all()`을 실행한다.
- 정식 Alembic migration은 아직 없다.
- 컬럼 변경·배포 순서·롤백이 필요한 운영 환경에서는 migration 도입이 필수다.

---

## 6. 화면별 구현 현황

| 프론트 경로 | 화면 | 구현 상태 | 실제/데모 구분 |
|---|---|---|---|
| `/login` | 로그인 | 구현 | 실제 인증 API |
| `/dashboard` | 전체 작업 | 구현 | DB 기반, 데모 시드 데이터 |
| `/dashboard/my-tasks` | 내 작업 현황 | 구현 | 로그인 사용자 기준 DB 조회 |
| `/dashboard/task-lookup` | 상세 조치 목록 | 구현 | DB 조회, alert metadata는 데모 |
| `/dev-dashboard` | 개발자 대시보드 | 부분 구현 | DB에 저장된 snapshot JSON |
| `/dev-dashboard/members` | 구성원 관리 | 조회 구현 | 직원 DB 조회, 수정 동작 미연결 |
| `/tasks/register` | 요구사항 등록 | 부분 구현 | POST는 실제, 등록 전 헤더/placeholder는 데모 |
| `/tasks/{requestNo}/runs/{runId}/analyzing` | 실시간 분석 | 부분 구현 | 실제 DB polling, 실행 worker 미연결 |
| `/tasks/review` | 요구사항 피드백 | 데모 | snapshot 표시, 승인/재요청 저장 미구현 |
| `/tasks/selection` | 데이터 선별 | 데모 | snapshot 표시 |
| `/tasks/sample-feedback` | 샘플 피드백 | 데모 | snapshot 표시, 피드백 저장 미구현 |
| `/tasks/processing` | 데이터 가공 | 데모 | snapshot 표시 |
| `/tasks/final-feedback` | 최종 피드백 | 데모 | snapshot 표시, 피드백 저장 미구현 |
| `/tasks/complete` | 작업 완료 | 데모 | snapshot 표시, 다운로드/배포 미구현 |

### 요구사항 등록 화면의 알려진 데모 의존성

`/tasks/register` 진입 시 `fetchTaskView('register')`를 호출한다. URL에 `requestNo`가 없으면 `REQ-2024-0847`을 fallback으로 사용하므로 신규 등록 전에 기존 데모 요청번호와 제목이 보인다.

수정 방향:

1. 신규 등록 화면에서는 기존 `RequestHeaderCard` 제거
2. 제목·요청 기관/요청자 입력 필드 추가
3. 로그인 직원 정보와 외부 고객/요청 기관 정보를 구분
4. `POST /api/v1/data-requests`에서 인증 직원의 `owner_id` 저장

---

## 7. Pipeline·Worker·Agent 구현 현황

### 준비된 것

- `data_requests`, `pipeline_runs`, `stage_runs`, `pipeline_events` 모델
- 요청 생성 및 최초 실행/단계/event 저장
- 프론트 실행 상태 polling 계약
- 실행기와 독립적인 `stage_runs` 상태·event 저장 구조
- 향후 AgentCore로 바꿀 때 유지할 이벤트·결과 계약 구조

### 아직 연결되지 않은 것

- 요청 생성 후 Celery task enqueue
- `pipeline.execute_stage` task
- Celery worker에서 오케스트레이터/요구사항 분석 에이전트 실행
- 에이전트 event를 `pipeline_events`에 기록
- stage/run progress 및 상태 전이
- 실패·재시도·취소·rollback 처리
- 에이전트 산출물을 `task_view_snapshots`로 projection
- SSE event stream
- AgentCore adapter 및 runtime session 연결

현재 브랜치에는 worker 실행 코드가 포함되어 있지 않다.

### 현재 별도로 존재하는 실행 코드

- `agent_runtime/requirements_analysis/`: 독립 요구사항 분석 에이전트
- `agent_runtime/data_selection/`: 데이터 선별 에이전트와 consistency 검사
- `agent_runtime/data_processing/`: 데이터 가공 runtime과 processor
- `app/domains/automation/`: 요구사항 분석 에이전트 단독 API
- `automation-supervisor-api/`: 요구사항 분석 → 데이터 선별 → 데이터 가공 → HITL dry-run이 가능한 별도 Supervisor 애플리케이션

에이전트 3단계 자체와 Supervisor dry-run은 동작하지만, 루트 FastAPI의
`pipeline_runs/stage_runs/pipeline_events` 및 Celery worker와는 아직 연결되지 않았다.

---

## 8. `juns`·`guns` 브랜치와의 차이

### Git 관계

2026-07-23 `origin/main` 병합 commit `a051ce7` 기준:

| 비교 브랜치 | 관계 | 의미 |
|---|---|---|
| `origin/main` | joungs-only 5, main-only 0 | 최신 main 병합 완료 |
| `origin/guns` | joungs-only 19, guns-only 0 | 데이터 가공 작업 포함, 현재 joungs의 조상 |
| `origin/juns` | joungs-only 19, juns-only 0 | 데이터 선별 작업 포함, 현재 joungs의 조상 |
| `origin/develop` | joungs-only 17, develop-only 0 | 현재 joungs의 조상 |

Frontend 저장소에는 현재 `main`과 `joungs`만 확인되며 별도 `juns`/`guns` remote branch는 없다.

### `joungs`에서 구조적으로 추가된 영역

- `app/domains/pipeline/`
- `app/domains/dashboard/`
- `app/pipeline/`
- 서비스 ERD 기반 async models
- 프론트 전용 dashboard/task view read API
- Celery/Redis docker-compose 구성
- 데모 사용자·대시보드·원천 데이터 seed scripts
- 프론트–백엔드 실제 통신 계약

### main 병합으로 들어온 주요 변경

- `agent_runtime/data_selection/`
- `agent_runtime/data_processing/`
- 요구사항 분석 prompt/consistency 개선
- `automation-supervisor-api`의 Strands client 및 dry-run 변경
- Supervisor validation 및 stub client 보완
- `Jenkinsfile`과 배포용 Dockerfile 변경
- 데이터 가공 agent 테스트

병합 충돌에서는 main이 삭제한 `.env.example`보다 팀 재현에 필요한 `joungs` 버전을
유지했다. `automation-supervisor-api/.env.example`도 같은 이유로 보존했다.

### 병합 후 함께 존재하는 두 구조

- main 계열: `agent_runtime/*`와 `automation-supervisor-api/`
- joungs 계열: 루트 `app/domains/pipeline`, `app/pipeline`, `app/domains/dashboard`

두 구조를 파일 단위로 합치는 작업은 끝났지만 실행 경로는 아직 분리되어 있다.
다음 통합 작업은 아래 원칙을 따른다.

1. `joungs`의 `app/domains/pipeline` DB/event 계약을 공통 실행 계약으로 사용한다.
2. `agent_runtime/*`는 FastAPI ORM을 직접 import하지 않는 독립 runtime으로 유지한다.
3. Celery task가 Supervisor/agent runtime을 호출하고 event만 DB에 기록한다.
4. AgentCore 전환 시 FastAPI response/event schema는 유지하고 runner 구현만 교체한다.
5. `.env` 파일은 커밋하지 않고 `.env.example`의 key 목록만 관리한다.

---

## 9. 우선순위가 높은 다음 작업

### P0: 실제 수직 흐름 완성

1. 요구사항 등록 화면의 데모 헤더 제거
2. 제목·요청 기관 입력 및 로그인 직원 `owner_id` 저장
3. `POST /api/v1/data-requests`에 인증 dependency 추가
4. 생성 직후 Celery `execute_stage` enqueue
5. 요구사항 분석 에이전트 결과를 stage/output/event에 저장
6. 분석 완료 후 review snapshot 생성

### P0: 인증 경계 정리

1. `/api/v1/dashboard*`, `/api/v1/runs*`, `/api/v1/tasks/*/views/*` 인증 적용
2. 개발자/관리자 화면 permission guard 적용
3. 프론트 401 → refresh → 원 요청 재시도 구현
4. refresh 실패 시 token 삭제 후 `/login` 이동
5. 로그아웃 메뉴/API 연결

### P1: HITL 쓰기 API

1. 요구사항 승인/재요청
2. 샘플 승인/수정 요청
3. 최종 산출물 승인/수정 요청
4. `reviews` 저장 및 run 상태 전이

### P1: 조회 모델 자동화

1. pipeline output → `task_view_snapshots` projection
2. dashboard counts/alerts를 metadata 문자열이 아니라 명시적 상태·기한으로 계산
3. 담당자 표시 문자열 대신 `owner_id`/employee relation 사용

### P2: 운영 준비

1. Alembic migration 도입
2. SSE endpoint 추가
3. artifact object storage 연결
4. AgentCore runner 구현
5. monitoring/metrics 및 retry 정책 연결

---

## 10. 로컬 실행 및 검증

### Backend

```bash
cd Backend-fastapi
cp .env.example .env
docker compose up -d db redis
.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### Frontend

```bash
cd Frontend_2
npm install
npm run dev
```

Frontend 환경값:

```env
VITE_API_BASE_URL=http://127.0.0.1:8000
```

### 데모 데이터

```bash
cd Backend-fastapi
.venv/bin/python -m scripts.seed_demo_user
.venv/bin/python -m scripts.seed_demo_data
.venv/bin/python -m scripts.seed_dashboard_demo
```

주의: `seed_demo_user`는 실행할 때 데모 비밀번호를 새로 생성한다.

### 현재 확인된 검증 결과

- Frontend `npm run lint`: 통과
- Frontend `npm run build`: 통과
- Backend Python compile: 통과
- main 병합 후 agent/pipeline 관련 테스트: 9개 통과
- Supervisor travel sample dry-run: 요구사항 분석 → 데이터 선별 → 데이터 가공 → HITL/rollback 통과
- 로그인 → 대시보드 → 내 작업 현황 API: 브라우저 통합 확인
- `/api/v1/dashboard/my-tasks`: 인증 상태에서 200 확인
- 전체 backend pytest: 현재 로컬 테스트 설정이 `127.0.0.1:5432`를 고정 사용하고 데모 DB는 `55432`에서 실행되어 DB 인증 단계에서 실패

테스트 DB URL을 명시적으로 분리하고 fixture에서 주입하도록 정리해야 전체 테스트 결과를 신뢰할 수 있다.

---

## 11. 공유 시 핵심 결론

1. 프론트가 읽는 대시보드와 단계별 상세 화면은 이제 FastAPI API를 거치지만, 일부 응답은 DB에 저장된 데모 snapshot이다.
2. 새 요구사항은 실제 service DB에 생성되지만 worker/agent 실행까지 이어지지 않는다.
3. 요구사항 분석 단독 API, Supervisor API, 신규 pipeline API가 병렬로 존재하므로 하나의 실행 계약으로 통합해야 한다.
4. main의 요구사항 분석·데이터 선별·데이터 가공 runtime은 병합됐지만 루트 Celery pipeline과는 아직 분리되어 있다.
5. 앞으로의 공통 기준은 `pipeline_runs` → `stage_runs` → `pipeline_events`이며, Celery와 AgentCore는 이 계약 뒤의 교체 가능한 runner로 두는 것이 안전하다.
