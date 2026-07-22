# 요구사항·작업 관리 API (FastAPI)

요구사항을 기준으로 다음 기능을 구현한 Python/FastAPI 백엔드입니다.

- 요구사항 CRUD
- 요구사항 상태 변경 및 상태 이력 관리
- 요구사항별 작업 CRUD
- 작업 담당자, 우선순위, 시작일·마감일 관리
- 작업 상태 변경 및 상태 이력 관리
- 요구사항 진행률 자동 계산
- 검색·필터·페이징
- PostgreSQL 기반 실행 (docker-compose로 DB 구동)
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

### 1. PostgreSQL (docker-compose)

deployment-host 호스트에 Docker/Docker Compose가 설치되어 있다고 가정한다. `.env`의
`POSTGRES_*` 값으로 컨테이너가 초기화되며, `employees/auth/session` 도메인용 DB
(`POSTGRES_DB`, 기본 `datamarket`)와 `requirements/tasks` 도메인용 DB
(`REQUIREMENTS_DB_NAME`, 기본 `requirements`)가 컨테이너 최초 기동 시 함께 생성된다
(`docker/postgres/init/01-create-additional-db.sh`).

```bash
docker compose up -d db
docker compose ps          # healthy 확인
```

- PostgreSQL은 호스트의 `localhost:5432` (기본값, `POSTGRES_PORT`로 변경 가능)로 노출된다.
- 데이터는 named volume `postgres_data`에 영구 보존된다.
- 운영 배포 전 `.env`의 `POSTGRES_PASSWORD`/`JWT_SECRET`/`BOOTSTRAP_ADMIN_PASSWORD`는
  반드시 새 값으로 교체할 것.

### 2. 백엔드

백엔드는 컨테이너가 아니라 deployment-host 호스트에서 직접 실행하며, `localhost:8000`으로 붙는다.
DB는 위에서 띄운 PostgreSQL(`localhost:5432`)을 `.env`의 `DATABASE_URL` /
`REQUIREMENTS_DATABASE_URL`로 사용한다. 앱 기동 시(`app/main.py`의 lifespan)
두 DB 모두 `create_all()`로 테이블이 자동 생성된다 (운영에서는 Alembic 등 마이그레이션 권장).

```bash
python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS/Linux
source .venv/bin/activate

pip install -r requirements.txt
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

기존 `Dockerfile`(gunicorn, 8000 포트)로 컨테이너 실행도 가능하다. 이 경우
`DATABASE_URL`/`REQUIREMENTS_DATABASE_URL`의 호스트를 `localhost` 대신
`host.docker.internal`(또는 deployment-host에서 docker gateway IP)로 바꿔야 컨테이너 안에서
호스트에 노출된 PostgreSQL에 접속할 수 있다.

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
