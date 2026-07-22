# 인증·권한관리 도메인 (`domains/auth`, `domains/employees`)

Spring Boot로 먼저 만들었던 `hana-employee-auth-backend`를 ARCHITECTURE.md의 역할 분담
("담당자 기능 전부: 로그인·권한(auth) → agent-service")에 맞춰 이 레포(FastAPI) 쪽으로
포팅한 것입니다. 기능은 100% 동일하게 이식했고, 아래 항목들은 실제로 pip install 하고
서버를 띄워서 검증까지 마쳤습니다.

## 기존 레포에 병합하는 방법

이 결과물은 **새 프로젝트를 통째로 만든 게 아니라, 기존 `app/` 밑에 끼워 넣을 도메인
모듈**입니다. 아래 파일들은 기존 레포에 이미 있을 가능성이 높아서 덮어쓰지 않고
별도로 드립니다 — 직접 병합해주세요.

- **`app/main.py`**: 여기 있는 건 테스트용으로 만든 최소 버전입니다. 실제로는 기존
  `main.py`에 다음 두 줄만 추가하면 됩니다.
  ```python
  from app.api.router import api_router
  app.include_router(api_router)
  ```
  그리고 앱 시작 시(`lifespan` 또는 startup 이벤트) `init_db()`와 `ensure_bootstrap_admin()`
  호출을 추가하세요 (`app.db.session`, `app.core.bootstrap`).
- **`.env.example`**: `.env.example.auth-addition` 파일 내용을 기존 `.env.example`에
  이어붙이세요.
- **`pyproject.toml`**: `dependencies`에 fastapi/sqlalchemy 등이 이미 있다면 버전만
  맞추고, `pyjwt`/`bcrypt`/`asyncpg`(비동기)·`psycopg2-binary`(동기)가 없다면 추가해주세요.

나머지(`app/core/config.py`, `app/core/security.py`, `app/core/bootstrap.py`,
`app/db/`, `app/common/`, `app/domains/auth/`, `app/domains/employees/`, 테스트 2개)는
새로 추가되는 파일이라 그대로 가져다 쓰시면 됩니다. 다만 `app/core/config.py`는 기존에
이미 `Settings` 클래스가 있다면 **필드를 합치는** 작업이 필요합니다(두 개의 `Settings`가
따로 있으면 안 됨).

## 실행해서 검증한 것들 (`tests/smoke_test_full_flow.py`, `tests/smoke_test_timeouts.py`)

Spring 버전에서 다뤘던 시나리오를 전부 실제로 pip install해서 돌려봤습니다.

```bash
pip install -e ".[dev]"
python tests/smoke_test_full_flow.py     # 로그인~권한관리 전체 플로우 25개 체크
python tests/smoke_test_timeouts.py       # 유휴시간/절대 타임아웃 실제 동작 확인
```

- 부트스트랩 관리자 로그인 → 비밀번호 강제 변경 전에는 업무 API 전부 403
- 비밀번호 변경 → 기존 세션 전체 폐기 확인
- 직원 생성(임시 비밀번호 16자) → 중복 ID 409
- 신규 직원 임시비밀번호 로그인 → 권한 없어서 403, `/me`는 예외적으로 허용
- 자기 자신의 권한관리 권한 제거 시도 400 차단, 자기 계정 비활성화 시도 400 차단
- 타 직원 권한 교체 → 기존 세션 즉시 무효화
- Refresh Token 갱신
- 로그인 5회 실패 → 계정 잠금
- **유휴시간 초과 시 401** (실제로 1초 대기시켜서 확인)
- **계속 활동 중이어도 절대 타임아웃(로그인 시각 기준) 지나면 401** (0.5초 간격으로
  계속 호출하면서 3초 뒤 끊기는지 확인)

## 포팅하면서 실제로 잡은 버그 2개

Java/Spring에서는 실행 환경이 없어서 코드 리딩으로만 검증했는데, 이번엔 실제로
pip install해서 돌려보다가 Python 생태계에만 있는 버그를 2개 잡았습니다.

1. **`passlib` + `bcrypt` 5.x 호환성 깨짐** — `passlib`은 더 이상 유지보수가 안 되는
   라이브러리라, 최신 `bcrypt`(5.x)에서 `__about__` 속성이 제거되면서 비밀번호 해시
   자체가 실패합니다. 그래서 `passlib` 없이 `bcrypt` 패키지를 직접 사용하도록
   했습니다 (`app/core/security.py`).
2. **SQLite에서 naive/aware datetime 비교 에러** — 타임존 정보를 붙인
   `datetime.now(timezone.utc)`를 SQLite에 저장했다가 다시 읽으면, SQLite가 타임존
   정보를 보존하지 못해서 naive datetime으로 돌아옵니다. 이걸 다시 tz-aware
   datetime과 비교하면 `TypeError: can't compare offset-naive and offset-aware
   datetimes`로 죽습니다. 그래서 앱 전체에서 "저장되고 비교되는 모든 datetime은
   UTC 기준 naive"라는 규약을 정하고 `app/common/time_utils.py`의 `utcnow()`
   하나로 통일했습니다. (운영에서 Postgres/MySQL을 쓰면 이 문제가 아예 안 보일 수도
   있어서, SQLite로 로컬 개발하지 않았다면 못 잡았을 버그입니다.)

## Spring 버전과 설계가 다른 지점

- **엔티티가 로직을 안 갖습니다.** Spring에서는 `Employee.recordLoginFailure()`처럼
  엔티티가 자기 상태를 바꾸는 메서드를 가진 "풍부한 도메인 모델"이었는데, 여기서는
  ARCHITECTURE.md의 "model: DB 매핑 (SQLAlchemy) — 테이블 생성 X" 원칙에 맞춰
  모델은 순수 데이터 매핑만 하고, 모든 비즈니스 로직은 `service/`로 옮겼습니다.
- **`@PreAuthorize` 대신 FastAPI `Depends`.** `require_permission(PermissionCode.X)`가
  Spring의 `@PreAuthorize("hasAuthority('X')")`에 대응합니다.
- **Spring Security의 세션 컨버터 → `get_current_auth` 의존성.** 매 요청마다
  JWT 디코딩 → 세션 조회 → 절대타임아웃/유휴시간/authVersion 체크 → 통과하면
  `touch()`로 활동시간 갱신, 이 흐름 자체는 Spring과 완전히 동일합니다
  (`app/common/security_deps.py`).

## 아직 안 한 것 (직접 하셔야 하는 부분)

- **DB 마이그레이션 도구(Alembic) 연결.** 지금은 `init_db()`가 `create_all()`로
  테이블을 만드는 로컬 개발용 방식입니다. 운영에서는 Alembic 등으로 바꾸는 걸
  권장합니다 (Spring 버전도 README에 같은 권장사항이 있었습니다).
- **기존 `main.py`/`core/config.py`와의 실제 병합.** 이건 레포 내용을 볼 수 없어서
  제가 대신 해드릴 수 없는 부분입니다.
- **ARCHITECTURE.md의 "데이터 계층 원칙"(anon/mart)은 이 도메인에 적용 안 했습니다.**
  그 원칙은 AI 파이프라인이 다루는 고객 거래 데이터의 익명화 등급에 관한 것이고,
  `employees`/`login_sessions`는 운영 계정 테이블이라 성격이 달라서 그대로 두는 게
  맞다고 판단했습니다. 혹시 다르게 생각하시면 말씀해주세요.
