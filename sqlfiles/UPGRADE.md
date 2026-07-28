# 로컬 DB 최신화 가이드

> 2026-07-28. `sqlfiles/`에 V007~V010이 추가되면서 기존 로컬 DB를 따라 올리는 방법을 정리했다.
> **이미 DB를 구축해 둔 사람용**이다. 처음 세팅하는 사람은 맨 아래 [처음 세팅하는 경우](#처음-세팅하는-경우)로.

---

## 0. 무엇이 추가됐나

| 파일 | 내용 | 왜 필요한가 |
|---|---|---|
| `V007` | `mart.anonymization_log` | 익명처리 이력. 날짜·항목·사유를 3년 보존해야 하는 법정 의무 |
| `V008` | `anon` 컬럼 코멘트 47개 | LLM이 읽는 스키마 설명. 이게 없으면 에이전트가 컬럼 의미를 모른다 |
| `V009` | `automation` 스키마 | Supervisor 실행 계층. 지금까지 `create_all`로 `public`에 만들던 것을 정식 스키마로 |
| `V010` | `agent_svc` 권한 축소 | 에이전트 계정이 실제 이메일 주소를 읽을 수 있던 것을 차단 |

`V002`·`V005`의 주석(k=3 근거)도 정정됐다. 주석만 바뀐 것이라 **다시 실행할 필요는 없다.**

---

## 1. 사전 준비

### 1-1. 코드 받기

```bash
git pull
pip install -r requirements.txt
```

### 1-2. `.env`에 키 추가

```bash
SUPERVISOR_SVC_PASSWORD=직접입력
```

`bootstrap.sh`가 `supervisor_svc` 롤을 만들 때 쓴다. 수동으로 롤을 만들 거면 없어도 된다.

### 1-3. `supervisor_svc` 롤 생성

`V009`가 이 롤에 권한을 주므로 **먼저 있어야 한다.**

```bash
psql -d portfolio -c "CREATE ROLE supervisor_svc LOGIN PASSWORD '직접입력';"
```

> 이미 있으면 `role already exists` 에러가 나는데 무시해도 된다.

---

## 2. Alembic 먼저 (항상 이 순서)

```bash
alembic upgrade head
```

**반드시 psql보다 먼저** 실행한다. `V010`이 `service.data_requests` 테이블에 권한을 거는데, 그 테이블은 Alembic이 만들기 때문이다.

확인:

```bash
alembic current
```

---

## 3. 적용 상태 확인

어디까지 적용됐는지 확인한다. **`f`로 나오는 것만** 다음 단계에서 실행하면 된다.

```bash
psql -d portfolio -c "
select
  to_regclass('mart.anonymization_log') is not null                    as \"V007\",
  (select count(*) from pg_description d
     join pg_class c     on c.oid = d.objoid
     join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'anon') > 40                                     as \"V008\",
  to_regnamespace('automation') is not null                            as \"V009\",
  not has_column_privilege('agent_svc','service.data_requests',
                           'sample_email','SELECT')                    as \"V010\",
  case when to_regclass('service.alembic_version') is null then '(미실행)'
       else (select version_num from service.alembic_version) end      as \"alembic\"
"
```

출력 예시:

```
 V007 | V008 | V009 | V010 |   alembic
------+------+------+------+--------------
 f    | f    | f    | f    | 9c81f4b7a2de
```

이 경우 V007~V010을 전부 실행하면 된다.

---

## 4. `f`인 것만 실행

전부 `portfolio_admin` 계정으로 실행한다.

```bash
psql -v ON_ERROR_STOP=1 -U portfolio_admin -d portfolio -f sqlfiles/migrations/V007__anonymization_log.sql
```

```bash
psql -v ON_ERROR_STOP=1 -U portfolio_admin -d portfolio -f sqlfiles/migrations/V008__anon_column_comments.sql
```

```bash
psql -v ON_ERROR_STOP=1 -U portfolio_admin -d portfolio -f sqlfiles/migrations/V009__automation_schema.sql
```

```bash
psql -v ON_ERROR_STOP=1 -U portfolio_admin -d portfolio -f sqlfiles/migrations/V010__agent_svc_column_grants.sql
```

**번호 순서대로** 실행할 것. V010은 V006 이후여야 하고, V006은 Alembic 이후여야 한다.

전부 끝나면 3번 확인 쿼리를 다시 돌려서 **전부 `t`** 인지 본다.

---

## 5. 앱 코드도 함께 바뀌었다 (Supervisor를 돌리는 사람)

`V009`는 `automation-supervisor-api` 코드 변경과 **한 몸이다.** 둘 중 하나만 적용하면 안 된다.
아래 5개 파일은 이 브랜치에 함께 들어 있으므로 `git pull`만 하면 반영된다.

| 파일 | 변경 | 이유 |
|---|---|---|
| `app/db/base.py` | `Base.metadata`에 `schema="automation"` | 스키마 지정이 없으면 `public`으로 간다 |
| `app/db/session.py` | `init_db()`의 `create_all` 제거 | 테이블 정의는 이제 `V009`가 소유한다 |
| `app/main.py` | `init_db()` 호출 제거 | 앱 시작 시 테이블을 만들지 않는다 |
| `app/domain/models.py` | `DateTime` → `DateTime(timezone=True)` | 전 계층이 UTC `timestamptz`다 |
| `app/core/config.py` | `database_url`을 `supervisor_svc@portfolio`로 | 기존 기본값 `appuser@appdb`는 존재하지 않는 계정·DB였다 |

### `.env`에 추가

```bash
DATABASE_URL=postgresql+psycopg://supervisor_svc:직접입력@127.0.0.1:5432/portfolio
```

`automation-supervisor-api`를 실행할 때 쓰는 값이다. 1-3에서 만든 비밀번호와 같아야 한다.

> ⚠️ 코드를 받기 전에 Supervisor를 띄운 적이 있으면 `public`에 테이블이 생겼을 수 있다.
> 아래 [자주 나오는 문제](#자주-나오는-문제)의 마지막 항목 참고.

---

## 처음 세팅하는 경우

기존 DB가 없다면 한 줄이면 된다.

```bash
./sqlfiles/bootstrap.sh --with-v001 --create-roles
```

`V001`~`V010` + `alembic upgrade head`를 알아서 순서대로 실행한다.
`.env`에 각 롤의 비밀번호(`AGENT_SVC_PASSWORD`, `APP_SVC_PASSWORD`,
`PORTFOLIO_ADMIN_PASSWORD`, `SUPERVISOR_SVC_PASSWORD`)가 있어야 한다.

---

## 자주 나오는 문제

### `role "supervisor_svc" does not exist`

1-3을 먼저 실행할 것.

### `relation "service.data_requests" does not exist` (V010에서)

Alembic을 먼저 안 돌린 경우다. `alembic upgrade head` 후 다시 실행.

### `must be owner of relation ...`

`-U portfolio_admin`을 빠뜨린 경우다. `COMMENT`나 `ALTER`는 소유자만 가능하다.

### `relation "..." already exists`

이미 적용된 마이그레이션이다. 3번 확인 쿼리로 다시 확인하고 건너뛴다.

### `public`에 `automation_jobs` 같은 테이블이 있다

Supervisor를 코드 수정 전에 띄운 적이 있다는 뜻이다. 데이터가 없으면 지우면 된다.

```bash
psql -d portfolio -c "
DROP TABLE IF EXISTS public.stage_artifact_caches,
                     public.automation_stage_runs,
                     public.automation_jobs CASCADE;"
```

---

## 왜 psql과 Alembic 두 가지를 쓰나

관리 대상이 겹치지 않는다.

```
Alembic    service 스키마만        모델(app/domains/*/model.py)이 정본
psql       mart / anon / automation  SQL이 정본
```

`service` 테이블은 SQLAlchemy 모델에서 파생되므로 Alembic이 맞고,
`mart`/`anon`은 트리거·CHECK·COMMENT·GRANT가 대부분이라 Alembic으로는 전부
`op.execute("...")` 문자열이 된다. 그래서 SQL 파일로 둔다.

예외는 **GRANT**다. `service` 테이블은 Alembic이 만들지만 권한(`V006`/`V010`)은
SQL 파일이 건다. 권한은 개인정보 경계라 DB 담당이 관리한다.
→ **그래서 항상 `alembic upgrade head`가 먼저다.**
