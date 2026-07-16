# 요구사항·작업 관리 API (FastAPI)

요구사항을 기준으로 다음 기능을 구현한 Python/FastAPI 백엔드입니다.

- 요구사항 CRUD
- 요구사항 상태 변경 및 상태 이력 관리
- 요구사항별 작업 CRUD
- 작업 담당자, 우선순위, 시작일·마감일 관리
- 작업 상태 변경 및 상태 이력 관리
- 요구사항 진행률 자동 계산
- 검색·필터·페이징
- SQLite 기반 로컬 실행
- Swagger UI 제공

## 상태

### 요구사항
- `DRAFT`: 초안
- `REVIEW`: 검토 중
- `APPROVED`: 승인
- `IN_PROGRESS`: 진행 중
- `COMPLETED`: 완료
- `REJECTED`: 반려
- `CANCELLED`: 취소

### 작업
- `TODO`: 할 일
- `IN_PROGRESS`: 진행 중
- `BLOCKED`: 차단
- `DONE`: 완료
- `CANCELLED`: 취소

## 실행

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
uvicorn app.main:app --reload
```

- Swagger UI: http://127.0.0.1:8000/docs
- Health Check: http://127.0.0.1:8000/health

## 테스트

```bash
pytest
```

## 주요 API

| 기능 | Method | URL |
|---|---|---|
| 요구사항 생성 | POST | `/api/v1/requirements` |
| 요구사항 목록 | GET | `/api/v1/requirements` |
| 요구사항 상세 | GET | `/api/v1/requirements/{id}` |
| 요구사항 수정 | PATCH | `/api/v1/requirements/{id}` |
| 요구사항 삭제 | DELETE | `/api/v1/requirements/{id}` |
| 요구사항 상태 변경 | PATCH | `/api/v1/requirements/{id}/status` |
| 요구사항 상태 이력 | GET | `/api/v1/requirements/{id}/status-history` |
| 작업 생성 | POST | `/api/v1/requirements/{id}/tasks` |
| 작업 목록 | GET | `/api/v1/requirements/{id}/tasks` |
| 작업 상세 | GET | `/api/v1/tasks/{task_id}` |
| 작업 수정 | PATCH | `/api/v1/tasks/{task_id}` |
| 작업 삭제 | DELETE | `/api/v1/tasks/{task_id}` |
| 작업 상태 변경 | PATCH | `/api/v1/tasks/{task_id}/status` |
| 작업 상태 이력 | GET | `/api/v1/tasks/{task_id}/status-history` |

## 인증 모듈과 병합할 때

현재 코드는 독립 실행 가능한 형태입니다. 기존 인증·권한 모듈에 병합할 경우 각 라우터의
`actor` 문자열을 로그인 사용자 정보로 교체하고, 라우터 의존성에 권한 검사를 추가하면 됩니다.

예시:

```python
@router.post(
    "",
    dependencies=[Depends(require_permission(PermissionCode.REQUIREMENT_WRITE))]
)
```

`created_by`, `updated_by`, 상태 변경 이력의 `changed_by`에는 현재 로그인 사용자 ID를 넣도록 연결하면 됩니다.
