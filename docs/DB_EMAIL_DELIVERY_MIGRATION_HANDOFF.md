# 이메일 발송 DB migration 담당 요청서

작성 기준: `feature/email-delivery-s3-customer-api` · `2026-08-11`

## 요청 배경

현재 이메일 발송 상태의 DB 소유자는 Spring이 아니라 FastAPI입니다. Spring은 DB에
접근하지 않고, FastAPI가 `service.email_deliveries`를 기록한 뒤 SQS 결과 메시지를
반영합니다.

이번 브랜치에서는 Alembic 파일을 추가하거나 수정하지 않았습니다. 브랜치에 임시로
들어갔던 `b6f1d9a4c8e2_add_email_deliveries.py`도 `develop` 기준을 유지하기 위해
제거했습니다. DB 담당자는 `develop` 최신 head를 기준으로 별도 migration을 작성해
주십시오.

## 필요한 테이블

```text
service.email_deliveries
```

애플리케이션 모델은
`Backend-fastapi/app/domains/pipeline/model.py`의 `EmailDelivery`입니다.

| 컬럼 | 요구사항 |
|---|---|
| `delivery_id` | `VARCHAR(36)` PK, UUID 문자열 |
| `run_id` | `BIGINT NOT NULL`, `service.pipeline_runs.id` 논리/외래키 참조 |
| `stage_attempt_no` | `SMALLINT NOT NULL`, 기본 1 |
| `requested_by` | `BIGINT NULL`, `service.employees.id` 참조 |
| `delivery_type` | `SELECTION_SAMPLE`, `FINAL_ARTIFACT` |
| `recipient` | `VARCHAR(254) NOT NULL` |
| `recipient_normalized` | `VARCHAR(254) NOT NULL` |
| `status` | `QUEUED`, `SENDING`, `SENT`, `DELIVERED`, `FAILED`, `BOUNCED`, `COMPLAINT` |
| `idempotency_key` | `VARCHAR(255) NOT NULL`, UNIQUE |
| `request_fingerprint` | SHA-256 hex 문자열 64자 |
| `sample_sha256` | `VARCHAR(64) NULL`; 최종 산출물은 빈 문자열일 수 있음 |
| `template_version` | `VARCHAR(50) NULL` |
| `provider_message_id` | `VARCHAR(255) NULL` |
| `failure_code` | `VARCHAR(80) NULL` |
| `attempt_count` | `SMALLINT NOT NULL`, 기본 0 |
| `next_retry_at` | `TIMESTAMPTZ NULL` |
| `delivered_at` | `TIMESTAMPTZ NULL` |
| `created_at` | `TIMESTAMPTZ NOT NULL`, 기본 `now()` 권장 |
| `updated_at` | `TIMESTAMPTZ NOT NULL`, 기본 `now()` 권장 |

현재 애플리케이션은 `attempt_count`와 `next_retry_at`를 직접 갱신하지 않습니다.
SQS visibility timeout와 DLQ가 재시도를 소유하므로, 당장은 컬럼을 유지하되 이
의미를 migration COMMENT에 기록해 주십시오.

## 권장 제약조건·인덱스

```sql
CHECK (delivery_type IN ('SELECTION_SAMPLE', 'FINAL_ARTIFACT'))
CHECK (status IN ('QUEUED', 'SENDING', 'SENT', 'DELIVERED', 'FAILED', 'BOUNCED', 'COMPLAINT'))
CHECK (attempt_count >= 0)
CHECK ((status = 'DELIVERED') = (delivered_at IS NOT NULL))

CREATE UNIQUE INDEX uq_email_deliveries_active_target
ON service.email_deliveries
  (run_id, stage_attempt_no, recipient_normalized, delivery_type)
WHERE status IN ('QUEUED', 'SENDING');

CREATE INDEX ix_email_deliveries_run_status
ON service.email_deliveries (run_id, status);
```

`updated_at`은 UPDATE 시 자동으로 현재 시각을 기록하는 트리거를 권장합니다.
단, 기존 공용 `service.trigger_set_updated_at()` 함수가 이미 있다면 재사용하고
동일한 함수를 중복 생성하지 마십시오.

## 개인정보·운영 정책

- `recipient`와 `recipient_normalized`는 개인정보이므로 보존기간과 파기 주체를
  DB 운영 정책에 반영해야 합니다. 현재 애플리케이션은 종료된 발송 건의 주소를
  기본 30일 뒤 `[REDACTED:{delivery_id}]` 형식의 PII-free 표식으로 치환합니다.
- request SQS 메시지에도 `recipient`가 포함되므로 queue message retention은
  1일로 설정하는 방향입니다.
- SQS DLQ에 들어간 발송 건은 자동 재발송하지 않습니다. 운영자가 확인한 뒤
  내부 API `POST /internal/v1/deliveries/{delivery_id}/dlq-close`를 호출하면
  `FAILED`와 `DLQ_MANUAL_CLOSE`로 종결됩니다.

## 적용 순서

1. `develop` 최신 head에서 migration 작성
2. 개발 DB에서 `alembic upgrade head` 실행
3. 위 제약조건·인덱스와 기존 `EmailDelivery` 모델의 autogenerate 차이 확인
4. FastAPI 이메일 API의 샘플/최종 산출물 발송 smoke test
5. 적용된 revision과 실제 DB 스키마 결과를 공유

이 브랜치의 애플리케이션 코드는 테이블이 이미 존재한다는 전제로 동작합니다.
Alembic revision을 이 브랜치에 다시 추가하지 않는 것이 협업 규칙입니다.
