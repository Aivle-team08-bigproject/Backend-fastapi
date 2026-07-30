# Automation Supervisor API

PostgreSQL 기반 자동화 프로세스 Supervisor 예제입니다.
Strands Agents SDK의 `Agent` 형태로 하위 agent를 호출하도록 구성했습니다.

이번 버전의 최종 QA는 LLM 자동 판정이 아니라 **사람 승인(HITL)** 입니다.

## 역할

- 요구사항 분석, 데이터 선별, CSV 조회, 데이터 가공 worker를 순서대로 호출
- 각 단계 완료 시 PostgreSQL에 상태와 산출물 저장
- 각 단계 산출물 검증
- 각 단계 산출물 캐싱
- CSV 원본 행은 DB나 LLM에 넣지 않고 경로·행 수·컬럼·SHA-256만 저장
- 각 worker 완료 후 `WAITING_HITL` 상태로 사람 승인 대기
- 사람이 승인하면 `COMPLETED`
- 사람이 반려하면 자연어 피드백을 `failure_code`로 분류
- `failure_code -> rollback_stage` 고정 정책으로 롤백 대상 결정
- 최대 반복 도달 시 `FAILED`

## 실행

```bash
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

DB 없이 로직만 확인:

```bash
python scripts\dry_run_supervisor.py
```

또는 Windows:

```cmd
run-dry-run.bat
```

다른 샘플로 확인:

```cmd
run-dry-run.bat --sample cafe
run-dry-run.bat --sample parenting
run-dry-run.bat --sample fitness
run-dry-run.bat --sample subscription
```

직접 요구사항을 넣어서 확인:

```cmd
run-dry-run.bat --requirement "30대 남성의 헬스 업종 월별 결제 변화를 차트와 CSV로 제공해줘."
```

선별된 거래 CSV를 넣어서 확인:

```cmd
run-dry-run.bat --sample travel --csv "C:\Users\User\Downloads\02_selected_transactions.csv"
```

CSV를 넣으면 `SELECTED_DATA_SUMMARY`가 출력되고, 이 요약이 `DATA_PROCESSING` 단계의 `processed_columns`, `api_result.meta`, `visualization`, `report`에 반영됩니다.

HITL 반려 피드백을 바꿔 롤백 정책 확인:

```cmd
run-dry-run.bat --sample subscription --feedback "가공 컬럼과 보고서 형식이 맞지 않습니다."
```

## API

작업 생성:

```http
POST /api/v1/supervisor/jobs
```

```json
{
  "raw_requirement": "해외 여행 결제 데이터를 CSV와 보고서로 제공해줘.",
  "source_csv_path": "/data/02_selected_transactions.csv"
}
```

`source_csv_path`는 Supervisor와 worker 컨테이너에 동일하게 마운트된 경로여야 하며,
기본적으로 `DATA_RETRIEVAL_ALLOWED_ROOT=/data` 아래의 `.csv`만 허용됩니다.

`DATA_RETRIEVAL` worker는 데이터 선별 Agent의 `selection_query.filters`를 실제 CSV에
적용하고 `DATA_RETRIEVAL_OUTPUT_ROOT`(기본 `/data/jobs`) 아래에 job별 `selected.csv`를
생성합니다. 가공 worker에는 이 선별 파일만 전달됩니다.

기본 필터 매핑:

```text
성별       -> gender
연령대     -> age_band
지역       -> resident_region
국가/목적지 -> destination_country_name
업종       -> spend_category, mcc_name
결제 채널  -> approval_channel
인증 방식  -> auth_method
소득 구간  -> annual_income_band
```

`여성 -> F`, `숙박 -> lodging/호텔·숙박` 같은 값 매핑도 결정론적 규칙으로
처리합니다. 매핑할 수 없는 필터가 있거나 필터 결과가 0행이면 worker가 실패하고
데이터 가공 단계로 진행하지 않습니다.

Supervisor Worker enqueue:

```http
POST /api/v1/supervisor/jobs/{job_id}/run
```

FastAPI는 Supervisor 로직을 직접 실행하지 않고 Redis Queue에 `run_supervisor_worker`
작업을 등록합니다. Worker Consumer가 Supervisor Worker를 실행하고, Supervisor Worker가
다음 단계 Worker를 DB에 `PENDING`으로 생성한 뒤 Redis에 `run_stage_worker`를 등록합니다.
단계 Worker는 결과를 DB에 저장하고 job을 `WAITING_HITL`로 바꾼 뒤 종료됩니다.

Lambda 호출에서는 `run_job` 또는 `submit_hitl_review` 응답의 `worker_id`를 다음
`run_worker` 이벤트의 `stage_id`로 전달하면 됩니다.

```json
{
  "action": "run_worker",
  "stage_id": 1
}
```

사람 승인/반려:

```http
POST /api/v1/supervisor/jobs/{job_id}/hitl-review
```

중간 단계가 승인되면 FastAPI가 Supervisor 로직을 다시 호출하여 다음 worker 하나를
`PENDING`으로 생성합니다. 반려되면 피드백을 `failure_code`로 분류하고 롤백 대상
worker 하나를 새로 생성합니다. 마지막 `DATA_PROCESSING` 단계가 승인된 경우에만 job이
`COMPLETED`가 됩니다.

상태 흐름:

```text
QUEUED
  -> (Supervisor) WORKER_CREATED
  -> (Worker) RUNNING
  -> (Worker 저장/종료) WAITING_HITL
  -> 승인: (Supervisor) 다음 WORKER_CREATED
  -> 반려: (Supervisor) 롤백 WORKER_CREATED
  -> 최종 가공 승인: COMPLETED
```

승인 예:

```json
{
  "approved": true,
  "reviewer": "manager01",
  "natural_feedback": "최종 산출물이 요구사항과 일치합니다."
}
```

반려 예:

```json
{
  "approved": false,
  "reviewer": "manager01",
  "natural_feedback": "선별된 데이터가 부족하고 테이블 선택이 요구사항과 맞지 않습니다."
}
```

상태 조회:

```http
GET /api/v1/supervisor/jobs/{job_id}
```

## Rollback Policy

Supervisor는 LLM에게 롤백 판단을 맡기지 않습니다.
검증 실패나 HITL 반려 피드백은 `failure_code`로 변환되고,
`app/policies/rollback_policy.py`의 고정 매핑으로 롤백 대상이 결정됩니다.

예:

```text
MISINTERPRETED_REQUIREMENT -> REQUIREMENT_ANALYSIS
INSUFFICIENT_DATA -> DATA_SELECTION
PROCESSING_RULE_INVALID -> DATA_PROCESSING
FORMAT_INVALID -> DATA_PROCESSING
OUTLIER_DETECTED -> DATA_SELECTION
```

## Agent Models

기본 모델:

```text
REQUIREMENT_ANALYSIS_MODEL=sonnet-4.6
DATA_SELECTION_MODEL=aws-nova
DATA_PROCESSING_MODEL=chatgpt-5.5
```

`app/adapters/model/strands_agent_client.py`에서 Strands `Agent`를 생성하고 호출합니다.
Strands가 설치되지 않은 로컬 환경에서는 같은 입출력 계약의 stub agent로 fallback 됩니다.

## Lambda 이식 포인트

`app/application/supervisor_service.py`의 `run_stage()`와 `submit_hitl_review()`가 Lambda handler에서 재사용할 수 있는 실행 단위입니다.

예시 event:

```json
{
  "action": "submit_hitl_review",
  "job_id": 1,
  "approved": false,
  "reviewer": "manager01",
  "natural_feedback": "데이터가 부족합니다."
}
```
