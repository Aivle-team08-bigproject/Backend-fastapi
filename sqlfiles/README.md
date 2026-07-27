# hanacard DB 구축 가이드

> 하나카드 맞춤형 데이터 자동화 가공 시스템 / 8조 3팀
> 이 번들은 백엔드 레포 안에서 앱 코드·Alembic과 함께 관리한다. 새 환경은 레포를 clone한 뒤
> 아래 부트스트랩 명령만으로 DB 구조를 구성할 수 있다. 원천 CSV는 개인정보 보호를 위해
> Git에 포함하지 않으며, 별도 권한 저장소에서 받은 경우에만 선택적으로 적재한다.

---

## 0. 전체 그림 — 스키마 3개, 도구 2개

| 스키마 | 내용 | 관리 도구 | 이유 |
|---|---|---|---|
| `mart` | 원본 가명데이터 (사람만 접근, LLM 차단) | **이 번들의 SQL** | 앱이 ORM으로 다루지 않음. 프라이버시 계층은 사람이 신중히 수동 실행하는 게 안전 |
| `anon` | mart의 k=3 익명화본 (LLM이 보는 유일한 계층) | **이 번들의 SQL** | 위와 같음 |
| `service` | 비즈니스·실행·산출 21개 테이블 | **Alembic** (레포 `alembic/`) | FastAPI가 ORM으로 다루므로 모델과 함께 버전관리 |

두 도구는 **서로 다른 스키마를 관리하므로 충돌하지 않는다**
(이력도 각각 분리: 이 번들은 수동 실행, Alembic은 `service.alembic_version`).

**파일명은 Flyway 규칙(`V<번호>__<설명>.sql`, 언더스코어 2개)을 따른다.**
지금은 psql로 실행하지만, 나중에 Flyway를 도입해도 파일을 그대로 쓸 수 있게 하기 위함이다.

---

## 1. 계정 3개

| 역할 | 용도 | 권한 경계 |
|---|---|---|
| `agent_svc` | LLM/에이전트 실행 | `anon` 전체 SELECT + `service` 실행계층 최소권한. **`mart` 접근 불가** |
| `app_svc` | 담당자 대면 서비스(FastAPI) | `service` CRUD. **`mart` 접근 불가** |
| `hanacard_admin` | 마이그레이션·배치 전용 | 세 스키마 소유자. 앱 런타임엔 사용 안 함 |

**핵심 원칙**: 마이그레이션은 **반드시 `hanacard_admin`으로 실행**한다.
PostgreSQL은 `CREATE TABLE`을 실행한 계정을 소유자로 삼기 때문에, 개인 계정으로
실행하면 그 사람만 이후 `ALTER`/`TRUNCATE`/트리거 제어가 가능해지고
Alembic·익명화 배치가 실패한다.

---

## 2. 신규 셋업 (팀원·새 환경)

```bash
# Docker PostgreSQL 실행
cp .env.example .env                         # 비밀번호는 로컬 값으로 변경
docker compose up -d db

# 역할·mart·anon·service·권한을 순서대로 구성
./sqlfiles/bootstrap.sh --with-v001 --create-roles

# 별도 전달받은 CSV가 seed/에 있을 때만 데이터 적재·검증
./sqlfiles/bootstrap.sh --with-v001 --create-roles --with-seed --with-verify
```

`--with-v001 --create-roles`는 신규 DB에서만 실행한다. 기존 DB는 `./sqlfiles/bootstrap.sh`를
사용하고, 소유권이 어긋난 기존 환경만 `patch/P001__align_existing_db.sql`을 별도로 검토한다.

### 선택 단계

```bash
# 데이터 적재 (CSV가 있는 경우에만 — 구조만 필요하면 생략 가능)
#   ⚠️ load_csv.sql은 psql의 \copy를 쓰는데, \copy의 상대경로는 "SQL 파일 위치"가
#      아니라 "psql을 실행한 디렉터리" 기준으로 해석된다. 반드시 seed/ 안에서 실행할 것.
#      (CSV 파일은 별도 권한 저장소에서 seed/에 받아 둔다)
cd seed && psql -U hanacard_admin -d hanacard -f load_csv.sql && cd ..

# 검증 리포트 (언제든 재실행 가능, DB를 변경하지 않음)
psql -U hanacard_admin -d hanacard -f verify/validation_report.sql

# `anon`은 이 번들에서 구조와 권한만 만든다. 현재 브랜치에는 별도 익명화 배치가
# 포함되어 있지 않으므로, 익명 데이터 적재가 필요하면 후속 배치 작업으로 추가한다.
```

---

## 3. 기존 환경 보정 (이미 구축한 사람만)

2026-07 이전에 개인 계정으로 구축한 로컬 DB는 소유권이 어긋나 있다.
**새로 구축하는 사람은 실행하지 말 것.**

```bash
psql -v ON_ERROR_STOP=1 -U postgres -d hanacard -f patch/P001__align_existing_db.sql
```

---

## 4. 앞으로 스키마를 바꿀 때

| 대상 | 방법 |
|---|---|
| `service` | 모델 수정 → `alembic revision --autogenerate -m "..."` → **생성 파일 검토** → `alembic upgrade head` |
| `mart` / `anon` | 이 번들에 `V007__...sql` 추가 (기존 파일 수정 금지 — 이미 적용한 사람과 어긋남) |

> ⚠️ Alembic autogenerate는 **CHECK 제약·트리거·GRANT를 감지하지 못한다.**
> 생성된 마이그레이션 파일을 반드시 눈으로 검토하고 필요하면 수동 추가할 것.

---

## 5. 파일 구성

```
migrations/   순서대로 실행하는 스키마 파일 (mart/anon + service GRANT)
seed/         데이터 적재 SQL — 마이그레이션이 아님, CSV는 별도 전달
verify/       검증 리포트 — DB를 바꾸지 않음, 언제든 재실행
patch/        기존 환경 보정 — 신규 환경에는 불필요
```

**분리 이유**: 데이터 적재와 검증은 "스키마 변경"이 아니라서 마이그레이션 체인에
섞이면 안 된다. 구조만 필요한 팀원이 데이터 없이도 셋업할 수 있어야 하고,
검증은 언제든 반복 실행할 수 있어야 한다.

---

## 6. 주의 사항

- **비밀번호는 어떤 SQL 파일에도 넣지 않는다.** `.env` 또는 시크릿 매니저로만 관리.
- **CSV 원천 데이터는 개인정보 보호를 위해 Git에 올리지 않는다.**
- 스키마 레벨 코멘트(`COMMENT ON SCHEMA`)는 **권한 없는 계정도 읽을 수 있다.**
  민감한 설계 근거(취약점 수준, 실측 수치)는 반드시 테이블/컬럼 레벨 코멘트에만 기재할 것.
- 대량 적재 시에는 정합성 트리거를 `DISABLE` → 적재 → 검증 → `ENABLE` 순으로 다룬다
  (익명화 배치가 이 절차를 자동으로 수행한다).
