# Backend FastAPI

AI 기반 데이터 활용 요청 플랫폼의 핵심 API와 비동기 파이프라인을 담당합니다. 인증·권한 관리, 요청 등록, 단계별 검토, 대시보드 조회 기능을 제공합니다.

## Responsibilities

- JWT 기반 인증과 직원·권한 관리
- 요구사항 분석 → 데이터 선별 → 데이터 가공의 단계별 워크플로
- Human-in-the-loop 검토와 반려 후 재시도 처리
- 작업 상태·결과 조회 API 및 실시간 상태 이벤트
- 개인정보 탐지와 산출물 검증

## Tech stack

Python 3.12, FastAPI, SQLAlchemy, Alembic, Celery, Redis, PostgreSQL, pytest

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
alembic upgrade head
uvicorn app.main:app --reload
```

환경 변수는 로컬 `.env`에만 설정합니다. 실제 접속 정보·API 키·비밀번호는 저장소에 커밋하지 않습니다.

```bash
pytest
```

## Structure

```text
app/                 # API, 도메인 서비스, 인증·권한
agent_runtime/       # 단계별 AI agent와 데이터 조회 계층
alembic/             # DB 마이그레이션
tests/               # API·도메인·파이프라인 테스트
```

## Related repositories

- [Frontend_2](https://github.com/Aivle-team08-bigproject/Frontend_2): 사용자·운영자 웹 UI
- [Backend-spring](https://github.com/Aivle-team08-bigproject/Backend-spring): 고객 산출물 전달 API
