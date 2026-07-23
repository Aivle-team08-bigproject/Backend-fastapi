# API 엔드포인트 목록

> 기준 브랜치: `joungs`  
> 상태 표기: **연동** = 백엔드 구현 및 프론트 사용, **백엔드만** = 구현됐지만 프론트 미사용, **예정** = 아직 미구현

## 1. 인증

| Method | Endpoint | 인증 | 상태 | 용도 |
|---|---|---|---|---|
| `POST` | `/api/auth/login` | 불필요 | 연동 | 직원 ID·비밀번호 로그인 및 Access Token 발급 |
| `GET` | `/api/auth/me` | 필요 | 연동 | 현재 사용자 이름·부서 조회 |
| `POST` | `/api/auth/refresh` | Refresh Cookie | 백엔드만 | Access Token 재발급 |
| `POST` | `/api/auth/logout` | 필요 | 백엔드만 | 현재 세션 로그아웃 |
| `POST` | `/api/auth/logout-all` | 필요 | 백엔드만 | 전체 세션 로그아웃 |
| `POST` | `/api/auth/change-password` | 필요 | 백엔드만 | 비밀번호 변경 |

## 2. 요청 및 Pipeline 실행

| Method | Endpoint | 인증 | 상태 | 용도 |
|---|---|---|---|---|
| `POST` | `/api/v1/data-requests` | 미적용 | 연동 | 요청·PipelineRun·StageRun·최초 Event 생성 |
| `GET` | `/api/v1/runs/{run_id}` | 미적용 | 연동 | 실행·단계·이벤트 상태 snapshot 조회 |
| `GET` | `/api/v1/data-requests` | 필요 예정 | 예정 | 요청 목록 조회 |
| `GET` | `/api/v1/data-requests/{request_no}` | 필요 예정 | 예정 | 요청 상세 조회 |
| `POST` | `/api/v1/runs/{run_id}/cancel` | 필요 예정 | 예정 | 실행 취소 |
| `POST` | `/api/v1/runs/{run_id}/retry` | 필요 예정 | 예정 | 전체 실행 재시도 |
| `POST` | `/api/v1/runs/{run_id}/stages/{stage_code}/retry` | 필요 예정 | 예정 | 특정 단계 재시도 |
| `PATCH` | `/api/v1/data-requests/{request_no}/assignee` | 필요 예정 | 예정 | 담당자 변경 |

## 3. 대시보드 및 작업 화면

| Method | Endpoint | 인증 | 상태 | 용도 |
|---|---|---|---|---|
| `GET` | `/api/v1/dashboard` | 미적용 | 연동 | 전체 작업·알림·인사이트 조회 |
| `GET` | `/api/v1/dashboard/my-tasks` | 필요 | 연동 | 현재 사용자의 담당 작업 조회 |
| `GET` | `/api/v1/dashboard/task-lookup` | 미적용 | 연동 | 상세 조치 대상 작업 조회 |
| `GET` | `/api/v1/dashboard/developer` | 미적용 | 연동 | 개발자 대시보드 snapshot 조회 |
| `GET` | `/api/v1/dashboard/members` | 미적용 | 연동 | 구성원·권한 조회 |
| `GET` | `/api/v1/tasks/{request_no}/views/{view_code}` | 미적용 | 연동 | 단계별 작업 화면 read model 조회 |

`view_code`: `register`, `analysis`, `review`, `selection`, `sample-feedback`, `processing`, `final-feedback`, `complete`

## 4. 검토·산출물·전달

| Method | Endpoint | 인증 | 상태 | 용도 |
|---|---|---|---|---|
| `POST` | `/api/v1/runs/{run_id}/reviews` | 필요 예정 | 예정 | HITL 검토 결과 제출 |
| `GET` | `/api/v1/runs/{run_id}/reviews` | 필요 예정 | 예정 | 실행별 검토 이력 조회 |
| `GET` | `/api/v1/runs/{run_id}/artifacts` | 필요 예정 | 예정 | 실행 산출물 목록 조회 |
| `GET` | `/api/v1/artifacts/{artifact_id}` | 필요 예정 | 예정 | 산출물 metadata 조회 |
| `GET` | `/api/v1/artifacts/{artifact_id}/download` | 필요 예정 | 예정 | 산출물 다운로드 |
| `POST` | `/api/v1/runs/{run_id}/deliveries` | 필요 예정 | 예정 | 산출물 전달 요청 |
| `GET` | `/api/v1/runs/{run_id}/deliveries` | 필요 예정 | 예정 | 전달 상태·이력 조회 |

## 5. SSE 실시간 이벤트

| Method | Endpoint | 인증 | 상태 | 용도 |
|---|---|---|---|---|
| `GET` | `/api/v1/runs/{run_id}/events/stream` | 필요 예정 | 예정 | 진행률·단계 변경·Agent log·산출물·실패 이벤트 스트림 |

SSE event type: `progress`, `stage.started`, `stage.completed`, `agent.log`, `artifact.ready`, `review.required`, `run.completed`, `run.failed`

SSE는 조회 알림만 담당한다. 요청 생성·검토·취소·재시도 같은 상태 변경은 REST API로 처리하고, 연결 실패 시 `GET /api/v1/runs/{run_id}` polling을 fallback으로 사용한다.

## 6. 요구사항 분석 Agent 단독 API

| Method | Endpoint | 인증 | 상태 | 용도 |
|---|---|---|---|---|
| `POST` | `/api/automation/requirements-analysis` | 필요 | 백엔드만 | 요구사항 분석 Agent 동기 실행 및 결과 저장 |
| `GET` | `/api/automation/requirements-analysis` | 필요 | 백엔드만 | 최근 분석 실행 목록 조회 |
| `GET` | `/api/automation/requirements-analysis/{run_id}` | 필요 | 백엔드만 | 분석 실행 단건 조회 |

이 API는 `/api/v1/data-requests` Pipeline과 아직 연결되지 않았다.

## 7. 직원·세션 관리

| Method | Endpoint | 인증·권한 | 상태 | 용도 |
|---|---|---|---|---|
| `GET` | `/api/admin/employees` | 필요 | 백엔드만 | 직원 목록 조회 |
| `GET` | `/api/admin/employees/{employee_code}` | 필요 | 백엔드만 | 직원 상세 조회 |
| `POST` | `/api/admin/employees` | 필요 | 백엔드만 | 직원 생성 |
| `PUT` | `/api/admin/employees/{employee_code}/permissions` | 필요 | 백엔드만 | 직원 권한 변경 |
| `PATCH` | `/api/admin/employees/{employee_code}/status` | 필요 | 백엔드만 | 직원 상태 변경 |
| `POST` | `/api/admin/employees/{employee_code}/reset-password` | 필요 | 백엔드만 | 비밀번호 초기화 |
| `GET` | `/api/admin/employees/permissions/catalog` | 필요 | 백엔드만 | 권한 목록 조회 |
| `GET` | `/api/admin/employees/audit-logs` | 필요 | 백엔드만 | 감사 로그 조회 |
| `GET` | `/api/admin/employees/{employee_code}/sessions` | 필요 | 백엔드만 | 직원 세션 조회 |
| `DELETE` | `/api/admin/sessions/{session_id}` | 필요 | 백엔드만 | 세션 강제 종료 |

## 8. 별도 Supervisor 애플리케이션

| Method | Endpoint | 상태 | 용도 |
|---|---|---|---|
| `POST` | `/api/v1/supervisor/jobs` | 별도 앱 | Supervisor 작업 생성 |
| `POST` | `/api/v1/supervisor/jobs/{job_id}/run` | 별도 앱 | Agent 단계 실행 |
| `POST` | `/api/v1/supervisor/jobs/{job_id}/hitl-review` | 별도 앱 | HITL 검토 제출 |
| `GET` | `/api/v1/supervisor/jobs/{job_id}` | 별도 앱 | 작업 상태 조회 |

`automation-supervisor-api/`는 현재 루트 FastAPI에 mount되지 않았고 프론트에서도 호출하지 않는다.
