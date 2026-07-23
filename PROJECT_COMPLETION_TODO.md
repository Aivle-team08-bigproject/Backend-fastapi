# Project Completion TODO

> 기준: 2026-07-23  
> Backend: `joungs@a051ce7`  
> Frontend: `joungs@4522465`

## 완료 목표

```text
요청 등록
→ Celery/AgentCore 에이전트 실행
→ 진행 상황 실시간 표시
→ 단계별 HITL 승인·수정
→ 최종 산출물 전달
→ 대시보드와 이력 조회
```

체크 기준:

- `[x]`: 현재 브랜치에서 코드와 기본 동작 확인
- `[ ]`: 미구현 또는 데모 데이터에만 의존

---

## 1. 기반 구조

- [x] FastAPI 루트 애플리케이션
- [x] PostgreSQL service DB 모델
- [x] JWT Access Token·Refresh Token·로그인 세션
- [x] 직원·권한·감사 로그 모델과 관리자 API
- [x] Redis·Celery worker Docker 구성
- [x] `AgentRunner` 공통 실행 계약
- [x] 요구사항 분석 agent runtime
- [x] 데이터 선별 agent runtime
- [x] 데이터 가공 agent runtime
- [x] Supervisor 3단계 실행·HITL·rollback dry-run
- [x] `main`의 agent runtime을 `joungs`에 병합
- [x] 데모 사용자·원천 데이터·대시보드 seed script
- [ ] Alembic migration 도입
- [ ] 개발·테스트·운영 DB 설정 분리
- [ ] 전체 pytest가 독립 테스트 DB에서 통과하도록 fixture 수정
- [ ] 환경변수 목록과 실행 명령 README 최신화

---

## 2. 요청 생성과 Pipeline DB

- [x] `data_requests` 모델
- [x] `pipeline_runs` 모델
- [x] `stage_runs` 모델
- [x] `pipeline_events` 모델
- [x] `agent_metrics`, `artifacts`, `reviews`, `deliveries` 모델
- [x] 새 요청 생성 시 `PipelineRun(QUEUED)` 생성
- [x] 최초 `StageRun(REQUIREMENT_ANALYSIS/PENDING)` 생성
- [x] 최초 접수 event 저장
- [x] run·stage·event 조회 API
- [ ] 요청 생성 API에 인증 dependency 적용
- [ ] 로그인 직원을 `data_requests.owner_id`에 저장
- [ ] 요청 제목·고객/요청 기관을 프론트에서 입력
- [ ] 요구사항 등록 화면의 `REQ-2024-0847` fallback 제거
- [ ] 동일 요청 재실행 시 `attempt_no` 증가 정책 구현
- [ ] 취소·재시도·rollback 상태 전이 service 구현

---

## 3. Worker와 Agent 실행

- [x] Celery app과 Redis broker/result backend 설정
- [x] worker 연결 확인용 `pipeline.health_check`
- [x] 독립 agent runtime 3종
- [x] 별도 Supervisor에서 agent 3단계 실행 검증
- [ ] `pipeline.execute_stage` Celery task 구현
- [ ] 요청 생성 직후 최초 task enqueue
- [ ] Worker에서 Supervisor/agent runtime 호출
- [ ] `AgentRunner`의 Local Celery adapter 구현
- [ ] stage 시작 시 `RUNNING`, 완료 시 `COMPLETED` 저장
- [ ] 실패 시 error와 `FAILED` event 저장
- [ ] agent log를 `pipeline_events`에 누적
- [ ] token·latency·cost를 `agent_metrics`에 저장
- [ ] 단계 완료 후 다음 단계 자동 enqueue
- [ ] Celery retry와 pipeline retry 정책 분리
- [ ] worker 중복 실행 방지 및 idempotency 적용
- [ ] AgentCore adapter 구현
- [ ] AgentCore session ID를 `executor_reference`에 저장

---

## 4. REST API 엔드포인트

REST API는 **명령 실행과 현재 상태 snapshot 조회**에 사용한다. 지속적인 진행 event 전달은 SSE 섹션에서 별도로 처리한다.

### 4.1 현재 구현된 API

- [x] `POST /api/auth/login`
- [x] `GET /api/auth/me`
- [x] `POST /api/auth/refresh`
- [x] `POST /api/auth/logout`
- [x] `POST /api/v1/data-requests`
- [x] `GET /api/v1/runs/{run_id}`
- [x] `GET /api/v1/dashboard`
- [x] `GET /api/v1/dashboard/my-tasks`
- [x] `GET /api/v1/dashboard/task-lookup`
- [x] `GET /api/v1/dashboard/developer`
- [x] `GET /api/v1/dashboard/members`
- [x] `GET /api/v1/tasks/{request_no}/views/{view_code}`

주의:

- refresh/logout API는 백엔드에 있지만 프론트에서 아직 사용하지 않는다.
- `my-tasks` 외 다수 `/api/v1` API에는 인증 dependency가 빠져 있다.
- task view payload는 현재 대부분 데모 snapshot이다.

### 4.2 추가하거나 보완할 명령 API

- [ ] `POST /api/v1/data-requests` 인증 및 owner 연결
- [ ] `POST /api/v1/runs/{run_id}/cancel`
- [ ] `POST /api/v1/runs/{run_id}/retry`
- [ ] `POST /api/v1/runs/{run_id}/stages/{stage_code}/retry`
- [ ] `POST /api/v1/runs/{run_id}/reviews`
- [ ] `POST /api/v1/runs/{run_id}/deliveries`
- [ ] `PATCH /api/v1/data-requests/{request_no}/assignee`

명령 응답은 작업 완료를 기다리지 않고 `202 Accepted`와 현재 `run_id`, 상태를 반환한다.

### 4.3 추가하거나 보완할 조회 API

- [ ] `GET /api/v1/data-requests`
- [ ] `GET /api/v1/data-requests/{request_no}`
- [ ] `GET /api/v1/runs/{run_id}/artifacts`
- [ ] `GET /api/v1/artifacts/{artifact_id}`
- [ ] `GET /api/v1/artifacts/{artifact_id}/download`
- [ ] `GET /api/v1/runs/{run_id}/reviews`
- [ ] `GET /api/v1/runs/{run_id}/deliveries`
- [ ] Dashboard API pagination·서버 필터
- [ ] Dashboard API의 데모 metadata 의존 제거
- [ ] task view를 agent output 기반 read model로 생성

### 4.4 API 공통 처리

- [ ] 모든 업무 API에 인증 적용
- [ ] 관리자·개발자 API에 permission guard 적용
- [ ] 공통 오류 응답 schema 확정
- [ ] 요청 idempotency key 지원
- [ ] pagination·filter·sort 규격 통일
- [ ] OpenAPI example과 endpoint 문서 작성
- [ ] API version 정책 확정

---

## 5. SSE 실시간 이벤트

SSE는 **진행률, 단계 변경, agent log, 산출물 준비, 실패 알림**만 전달한다. 요청 생성·승인·취소 같은 쓰기 작업은 REST API를 유지한다.

### 5.1 SSE 백엔드

- [ ] `GET /api/v1/runs/{run_id}/events/stream` 추가
- [ ] 응답 `Content-Type: text/event-stream`
- [ ] 인증된 사용자가 해당 run을 볼 권한이 있는지 검사
- [ ] `pipeline_events.id`를 SSE `id`로 사용
- [ ] event type 규격 정의
  - [ ] `progress`
  - [ ] `stage.started`
  - [ ] `stage.completed`
  - [ ] `agent.log`
  - [ ] `artifact.ready`
  - [ ] `review.required`
  - [ ] `run.completed`
  - [ ] `run.failed`
- [ ] SSE `data` JSON schema 정의
- [ ] `Last-Event-ID` 이후 DB event 재전송
- [ ] 주기적 heartbeat 전송
- [ ] terminal 상태에서 마지막 event 후 연결 종료
- [ ] 클라이언트 연결 해제 감지
- [ ] DB polling 또는 Redis Pub/Sub 중 전달 방식 결정
- [ ] 다중 FastAPI instance에서도 event 누락 없는 구조 구현
- [ ] SSE endpoint 통합 테스트

권장 event 예시:

```text
id: 128
event: stage.completed
data: {"run_id":12,"stage_code":"REQUIREMENT_ANALYSIS","progress_percent":33}
```

### 5.2 Worker의 SSE 지원 작업

Worker가 SSE connection을 직접 관리하지 않는다. Worker는 event를 DB에 저장하고, FastAPI SSE endpoint가 이를 전달한다.

- [ ] 모든 상태 변경과 log를 `pipeline_events`에 먼저 저장
- [ ] event 저장 후 Redis channel publish 여부 결정
- [ ] DB commit 완료 전 event가 전달되지 않도록 보장
- [ ] 동일 event 중복 저장 방지
- [ ] payload에서 secret·PII 제거

### 5.3 프론트 SSE

- [ ] 분석 진행 화면의 3초 polling을 SSE로 교체
- [ ] 인증 헤더 제약을 고려한 SSE client 방식 결정
- [ ] 연결 재시도와 backoff 구현
- [ ] 마지막 event ID 유지
- [ ] event 수신 시 timeline·log·progress 즉시 갱신
- [ ] SSE 연결 실패 시 `GET /runs/{run_id}` polling fallback
- [ ] 화면 이탈 시 connection 정리
- [ ] 완료·실패 event 수신 시 최종 snapshot 재조회

---

## 6. HITL과 산출물

- [x] 별도 Supervisor에서 승인·반려와 rollback 정책 검증
- [x] `reviews`, `artifacts`, `deliveries` DB 모델
- [ ] 요구사항 분석 결과 승인·수정 API
- [ ] 샘플 데이터 승인·수정 API
- [ ] 최종 산출물 승인·수정 API
- [ ] 자연어 피드백 저장
- [ ] failure code와 rollback stage 매핑
- [ ] 수정 요청 후 새 stage attempt 생성
- [ ] artifact object storage 연동
- [ ] checksum·크기·MIME type 저장
- [ ] PII scan 상태 관리
- [ ] 다운로드 또는 API 전달 구현

---

## 7. 프론트엔드

- [x] 로그인 화면과 실제 로그인 API
- [x] 전체 작업 대시보드 DB 연동
- [x] 내 작업 현황 로그인 사용자 기준 연동
- [x] 상태·담당자·등록 월 필터
- [x] 프로필 메뉴의 새 작업·관리자 페이지 이동
- [x] 작업 상세 화면 라우팅
- [x] 새 요청 생성 후 `run_id` 화면 이동
- [x] 실행 상태 3초 polling
- [ ] 요구사항 등록 화면 데모 header 제거
- [ ] 제목·요청 기관 입력
- [ ] 승인·수정 요청 버튼을 REST API에 연결
- [ ] 단계별 snapshot을 실제 pipeline 결과로 표시
- [ ] refresh token 자동 갱신
- [ ] 401 공통 처리와 로그인 이동
- [ ] 로그아웃 메뉴 연결
- [ ] 관리자 화면 permission 기반 접근 제한
- [ ] loading·empty·error 상태 통일
- [ ] SSE 진행 화면 연결

---

## 8. 보안·운영·배포

- [x] 비밀번호 hash와 로그인 실패 잠금
- [x] 세션 idle/absolute timeout
- [x] 관리자 permission dependency
- [x] CORS 환경설정
- [x] Dockerfile과 Jenkinsfile
- [ ] 업무 API authorization 범위 검사
- [ ] Refresh Cookie 운영 환경 Secure 설정
- [ ] secret manager 적용
- [ ] API rate limit
- [ ] 감사 로그 범위 확장
- [ ] 구조화 로그와 correlation ID
- [ ] Prometheus/CloudWatch metric
- [ ] 에이전트 비용·latency dashboard
- [ ] PostgreSQL backup·restore 절차
- [ ] CI에서 frontend/backend test 실행
- [ ] staging 배포와 end-to-end test
- [ ] AgentCore 운영 전환 test

---

## 9. 권장 구현 순서

1. 요구사항 등록 화면과 `POST /data-requests`의 인증·owner 연결
2. Celery `execute_stage`와 Supervisor adapter 연결
3. 요구사항 분석 한 단계의 DB event/output 저장 완성
4. 세 agent 단계 자동 전환
5. REST HITL 명령 API
6. task view projection과 프론트 상세 화면 연결
7. SSE backend와 프론트 연결
8. artifact 전달
9. 인증·권한·migration·관측성 보완
10. AgentCore adapter와 staging 배포

---

## 10. 프로젝트 완료 기준

- [ ] 새 요청이 실제 에이전트 3단계를 자동 실행한다.
- [ ] 모든 단계 상태와 event가 DB에 남는다.
- [ ] 프론트가 진행 상황을 실시간 표시한다.
- [ ] HITL 승인·수정 요청과 rollback이 동작한다.
- [ ] 최종 산출물을 조회·다운로드 또는 API로 전달할 수 있다.
- [ ] 재시작·재연결 후에도 DB event로 상태를 복구한다.
- [ ] 사용자·관리자 권한이 API와 화면 모두에서 강제된다.
- [ ] migration, test, monitoring, backup 절차가 준비된다.
- [ ] Celery와 AgentCore가 같은 실행 계약을 사용한다.
