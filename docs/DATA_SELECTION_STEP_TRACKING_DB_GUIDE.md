# 데이터 선별 Agent 단계 추적 계약 — 기존 DB 재사용

## 결정 사항

데이터 선별 Agent의 세 의미적 단계를 추적하되 Neon DB 스키마는 변경하지 않는다.

```text
SOURCE_COLUMN_SELECTION
→ DERIVED_COLUMN_DESIGN
→ SYNTHETIC_SAMPLE_GENERATION
```

별도 테이블이나 컬럼을 추가하지 않고 기존 테이블을 다음과 같이 사용한다.

| 기존 테이블 | 역할 |
|---|---|
| `service.pipeline_runs` | 전체 실행 상태, 현재 상위 단계, 진행률 |
| `service.stage_runs` | DATA_SELECTION 현재 상태 snapshot과 최종 결과 |
| `service.pipeline_events` | 서브스텝 상태 변경 이력 및 SSE event ID |
| `service.reviews` | HITL 승인·반려와 자연어 feedback |

## StageRun snapshot

`DATA_SELECTION` 실행 중 `stage_runs.output_payload.selection_steps`에 현재 상태를 저장한다.

```json
{
  "selection_steps": {
    "SOURCE_COLUMN_SELECTION": {
      "status": "COMPLETED",
      "started_at": "2026-08-04T12:00:00+09:00",
      "completed_at": "2026-08-04T12:00:08+09:00",
      "metadata": {
        "selected_table_count": 2,
        "source_column_count": 7
      },
      "error_message": null
    },
    "DERIVED_COLUMN_DESIGN": {
      "status": "RUNNING",
      "started_at": "2026-08-04T12:00:08+09:00",
      "completed_at": null,
      "metadata": null,
      "error_message": null
    },
    "SYNTHETIC_SAMPLE_GENERATION": {
      "status": "PENDING",
      "started_at": null,
      "completed_at": null,
      "metadata": null,
      "error_message": null
    }
  }
}
```

중간 Agent 산출물 전체는 DB에 저장하지 않고 한 Celery 태스크의 메모리 안에서 다음
프롬프트로 전달한다. DB에는 상태, 실패 사유, 작은 요약 지표만 저장한다.

세 단계가 완료되면 기존 최종 선별 결과와 `selection_steps` snapshot을 같은
`output_payload`에 함께 저장한다. 기존 샘플 API와 가공 Agent는 기존 최상위 결과 필드를
계속 사용한다.

## 상태 및 진행률

```text
PENDING · RUNNING · COMPLETED · FAILED
```

| 상태 변경 | 진행률 |
|---|---:|
| 원본 컬럼 선별 시작/완료 | 34 / 44 |
| 파생 컬럼 정의 시작/완료 | 45 / 55 |
| 합성 샘플 생성 시작/완료 | 56 / 66 |

HITL 반려는 상위 `StageRun.status = ROLLED_BACK`으로 표현한다. 이전 StageRun의
`output_payload.selection_steps`는 그대로 보존하고, 새 attempt의 StageRun은 빈
`output_payload`에서 새로운 snapshot을 시작한다.

## 이벤트와 SSE

서브스텝 상태 변경마다 기존 `pipeline_events`에 행을 추가한다.

```json
{
  "stage": "DATA_SELECTION",
  "selection_step": "DERIVED_COLUMN_DESIGN",
  "selection_step_status": "RUNNING",
  "stage_run_id": 93,
  "attempt_no": 2,
  "progress_percent": 45,
  "step_metadata": null
}
```

`pipeline_events.id`를 SSE `event_id`로 사용한다. SSE 재접속 시 DB의 최신
`stage_runs.output_payload.selection_steps`를 snapshot으로 보내고, `Last-Event-ID` 이후
이벤트를 기존 `pipeline_events`에서 복구한다.

## 저장 및 발행 순서

```text
stage_runs.output_payload.selection_steps 갱신
→ pipeline_runs 진행률 갱신
→ pipeline_events 추가 및 ID 확정
→ DB commit
→ Redis Pub/Sub
→ FastAPI SSE
```

DB가 상태의 정본이며 Redis는 실시간 전달 통로다. Redis 발행이 실패해도 DB snapshot으로
복구할 수 있다. 이 설계에는 Alembic 마이그레이션이나 Neon 스키마 변경이 필요하지 않다.
