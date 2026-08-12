# AgentCore 실행 계약

기준 브랜치: `develop_aws`

이 문서는 Celery 제거 작업 전에 FastAPI와 Bedrock AgentCore Runtime 사이에
고정하는 wire contract다. 현재 구현은 Celery가 orchestration을 유지하면서 각
stage를 이 계약으로 AgentCore에 위임한다. 다음 단계에서 orchestration을 FastAPI
직접 호출로 옮겨도 payload와 상태 상관관계는 그대로 유지한다.

## 실행 식별자

`execution_id`는 하나의 파이프라인 실행 시도(attempt)를 식별하는 불변 문자열이다.
애플리케이션은 UUID를 발급하며, wire contract는 마이그레이션 기간의 기존 식별자도
수용할 수 있도록 1~255자 범위를 허용한다.

- 최초 실행, HITL 재개, 실패 후 재시도는 각각 새 `execution_id`를 발급한다.
- 이전 실행의 이벤트와 결과는 현재 `execution_id`와 일치하지 않으면 무시한다.
- `PipelineRun`의 기존 `celery_task_id`는 전환 기간 동안 같은 값을 담지만, 외부 계약의
  의미는 Celery task ID가 아니라 execution ID다.
- AgentCore `runtimeSessionId`는 transport/session 식별자이며 `execution_id`와 동일시하지 않는다.

## FastAPI → AgentCore

`POST /invocations` 요청 envelope:

```json
{
  "agent_name": "data-selection-agent",
  "model_name": "",
  "execution_id": "4d6f0a1e-6e7b-4a77-b4d2-9b2f5f9e2bd6",
  "payload": {}
}
```

허용 agent는 `requirement-analysis-agent`, `data-selection-agent`,
`data-processing-agent` 세 가지다. stage별 payload는 각 stage의 기존 validation
계약을 따르며, invocation envelope에는 DB URL, AWS access key, JWT, 원문 secret을
넣지 않는다.

`data-processing-agent` payload의 `artifact_context.pipeline_run_id`는 양의 정수여야
하며, 유효한 CSV는 Runtime이 S3에 저장하고 본문 대신 storage metadata를 반환한다.

## AgentCore → FastAPI

성공 응답은 다음 형태만 허용한다.

```json
{ "output": { "...": "stage result" } }
```

Celery Worker는 stage별 최종 validation, HITL 전이, Artifact·delivery 기록을 담당한다.
Runtime은 실행 중인 내부 checklist와 기술 로그를 NeonDB의 `PipelineRun`, `StageRun`,
`PipelineEvent`에 직접 기록한다. 이때 Runtime은 `execution_id`로 현재 attempt를 다시
검증하고, Redis에는 연결하지 않는다.

실패 시 Runtime은 원문 예외·payload·credential을 응답하지 않고 HTTP 500과 다음 내부
계약을 사용한다.

```json
{
  "error": "agent_execution_failed",
  "retryable": true,
  "message": "agent execution failed"
}
```

호출 adapter는 네트워크 오류, 잘못된 content type, 잘못된 JSON, `output` 누락을 모두
`AgentCoreInvocationError`로 정규화한다. 로그에는 `agent_name`, 오류 유형, 실행 ID만
남기고 payload 본문은 남기지 않는다.

## Redis SSE 상태 이벤트

Redis는 Worker가 발행하는 화면 갱신용 상태 이벤트 채널이다. AgentCore Runtime은 Redis
권한 없이 PostgreSQL 이벤트만 commit하며, FastAPI SSE가 1초 polling으로 Runtime 이벤트를
같은 stream에 합친다.
이벤트에는 반드시 `run_id`, `execution_id`(전환 전에는 `celery_task_id` 필드에 저장),
`event_kind`, `occurred_at`, `run_status`, `progress_percent`를 포함한다.

- DB 상태를 먼저 commit한 뒤 Worker 이벤트만 Redis에 발행한다.
- Runtime 이벤트는 DB의 `PipelineEvent.id`를 SSE cursor로 사용해 Redis 이벤트와 중복 없이
  전달한다.
- recorder는 현재 run의 execution ID와 다른 이벤트를 stale 이벤트로 버린다.
- `status`는 timeline 상태 전이, `agent_log`는 기술 로그다.
- 원천 데이터 행, credential, LLM secret은 이벤트에 넣지 않는다.

## HITL·재시도·timeout

- HITL 승인/거절은 현재 실행을 수정하지 않고 새 attempt와 새 execution ID를 만든다.
- AgentCore 호출 timeout은 API 요청 timeout과 분리한다. 기본 Runtime read timeout은 900초다.
- 네트워크·5xx·429는 제한된 횟수로 재시도할 수 있다.
- validation 실패, 정책 위반, privacy threshold 미달은 자동 재시도하지 않고 failure code와
  rollback 대상 stage를 기록한다.
- 최종 실패 시 DB의 `error_message`에는 사용자에게 필요한 요약만 저장하고 원문 예외는
  구조화된 운영 로그에서만 확인한다.

## 전환 순서

1. 현재 Celery task가 이 계약의 caller로 동작하는지 검증한다.
2. `celery_task_id`를 `execution_id`로 이름만 중립화한다.
3. FastAPI 직접 호출 경로를 추가한다.
4. HITL·재시도·SSE 테스트를 직접 호출 경로로 옮긴다.
5. 마지막에 Celery broker/task와 관련 설정을 제거한다.
