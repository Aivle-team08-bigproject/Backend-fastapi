# Automation Supervisor API

PostgreSQL 기반 자동화 프로세스 Supervisor 예제입니다.
Strands Agents SDK의 `Agent` 형태로 하위 agent를 호출하도록 구성했습니다.

이번 버전의 최종 QA는 LLM 자동 판정이 아니라 **사람 승인(HITL)** 입니다.

## 역할

- 요구사항 분석, 데이터 선별, 데이터 가공 agent를 순서대로 호출
- 각 단계 완료 시 PostgreSQL에 상태와 산출물 저장
- 각 단계 산출물 검증
- 각 단계 산출물 캐싱
- 데이터 가공 완료 후 `WAITING_HITL` 상태로 사람 승인 대기
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

자동 오케스트레이션 실행:

```http
POST /api/v1/supervisor/jobs/{job_id}/run
```

이 호출은 `DATA_PROCESSING`까지 실행한 뒤 `WAITING_HITL` 상태로 멈춥니다.

사람 승인/반려:

```http
POST /api/v1/supervisor/jobs/{job_id}/hitl-review
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
