# 로컬 DB 최신화 가이드

> 2026-07-28. `sqlfiles/`에 V007~V010이 추가되면서 기존 로컬 DB를 따라 올리는 방법을 정리했다.
> **이미 DB를 구축해 둔 사람용**이다. 처음 세팅하는 사람은 맨 아래 [처음 세팅하는 경우](#처음-세팅하는-경우)로.

---

## 0. 무엇이 추가됐나

| 파일 | 내용 | 왜 필요한가 |
|---|---|---|
| `V007` | `mart.anonymization_log` | 익명처리 이력. 날짜·항목·사유를 3년 보존해야 하는 법정 의무 |
| `V008` | `anonymized` 컬럼 코멘트 47개 | LLM이 읽는 스키마 설명. 이게 없으면 에이전트가 컬럼 의미를 모른다 |
| `V009` | `automation` 스키마 | Supervisor 실행 계층. 지금까지 `create_all`로 `public`에 만들던 것을 정식 스키마로 |
| `V010` | `agent_svc` 권한 축소 | 에이전트 계정이 실제 이메일 주소를 읽을 수 있던 것을 차단 |
| `V011` | `automation` 상태 CHECK 완화 | **V009가 상태값을 열거해서, 새 상태(`SUPERVISOR_QUEUED` 등)를 쓰면 DB가 거부하던 문제** |

> ⚠️ **`V009`를 이미 적용하셨다면 `V011`을 반드시 함께 적용하세요.**
> `V009`만 적용된 상태에서는 파이프라인이 `SUPERVISOR_QUEUED` / `WORKER_CREATED` /
> `REJECTED` / `DATA_RETRIEVAL` 을 쓰는 순간 `check constraint violation`으로 실패합니다.

`V002`·`V005`의 주석(k=3 근거)도 정정됐다. 주석만 바뀐 것이라 **다시 실행할 필요는 없다.**

---

## 1. 사전 준비

### 1-1. 코드 받기

```bash
git pull
pip install -r requirements.txt
```

### 1-2. `.env`에 키 추가

노션에서 받은 `.env`에 아래 키가 있는지 확인하고, 없으면 추가한다.
**값은 팀에서 정한 실제 비밀번호를 넣는다** — 아래는 예시일 뿐이다.

```bash
SUPERVISOR_SVC_PASSWORD=<팀 공용 비밀번호>
```

`bootstrap.sh`가 롤을 만들 때, 그리고 Supervisor가 DB에 접속할 때 쓴다.

### 1-3. `supervisor_svc` 롤 생성

`V009`가 이 롤에 권한을 주므로 **먼저 있어야 한다.**

⚠️ **비밀번호를 직접 타이핑하지 말 것.** `.env` 값을 그대로 읽어서 만든다.
그래야 DB와 `.env`가 어긋나지 않는다.

```bash
set -a && source .env && set +a && \
psql -d portfolio -c "CREATE ROLE supervisor_svc LOGIN PASSWORD '$SUPERVISOR_SVC_PASSWORD';"
```

이미 롤이 있는데 비밀번호가 다르다면:

```bash
set -a && source .env && set +a && \
psql -d portfolio -c "ALTER ROLE supervisor_svc LOGIN PASSWORD '$SUPERVISOR_SVC_PASSWORD';"
```

> **왜 이렇게까지 하나**: 로컬 `pg_hba`가 대개 `trust`라 **비밀번호가 틀려도 접속이 됩니다.**
> 그래서 어긋난 걸 모르고 지내다가, `docker compose`로 띄우거나 배포할 때
> 한꺼번에 인증 실패가 납니다. 확인 방법은 아래 [비밀번호 정합성 확인](#비밀번호-정합성-확인) 참고.

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
    where n.nspname = 'anonymized') > 40                                     as \"V008\",
  to_regnamespace('automation') is not null                            as \"V009\",
  not has_column_privilege('agent_svc','service.data_requests',
                           'sample_email','SELECT')                    as \"V010\",
  (to_regnamespace('automation') is not null
     and not exists (select 1 from pg_constraint
                      where conname = 'ck_automation_jobs_status'))    as \"V011\",
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

```bash
psql -v ON_ERROR_STOP=1 -U portfolio_admin -d portfolio -f sqlfiles/migrations/V011__automation_relax_status_checks.sql
```

**번호 순서대로** 실행할 것. V010은 V006 이후여야 하고, V006은 Alembic 이후여야 한다.
V011은 V009가 만든 제약을 고치는 것이므로 V009 이후여야 한다.

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

## 비밀번호 정합성 확인

로컬 `pg_hba`가 `trust`면 **비밀번호가 틀려도 접속이 된다.** 그래서 `.env`와 DB가
어긋나도 평소엔 아무 증상이 없다가, `docker compose`로 띄우거나 배포하는 순간
전부 인증 실패가 난다. (컨테이너 PostgreSQL은 `scram-sha-256`을 쓴다)

아래 스크립트가 `.env` 값과 DB에 저장된 해시를 비교한다. **비밀번호는 화면에 찍히지 않는다.**

```bash
python3 - <<'PY'
import base64, hashlib, hmac, unicodedata, subprocess, urllib.parse
def sk(pw, salt, it):
    p = unicodedata.normalize('NFKC', pw).encode()
    s = hashlib.pbkdf2_hmac('sha256', p, salt, it)
    return base64.b64encode(hashlib.sha256(
        hmac.new(s, b'Client Key', hashlib.sha256).digest()).digest()).decode()

env = {}
for line in open('.env', encoding='utf-8', errors='replace'):
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, v = line.split('=', 1); env[k.strip()] = v.strip()
pw = lambda k: urllib.parse.unquote(urllib.parse.urlparse(env[k]).password or '')

targets = [
    ('agent_svc',      pw('PORTFOLIO_AGENT_DATABASE_URL')),
    ('app_svc',        pw('PORTFOLIO_APP_DATABASE_URL')),
    ('portfolio_admin', pw('PORTFOLIO_MIGRATION_DATABASE_URL')),
    ('supervisor_svc', env.get('SUPERVISOR_SVC_PASSWORD', '')),
]
for role, p in targets:
    o = subprocess.run(['psql','-d','portfolio','-At','-c',
        f"select rolpassword from pg_authid where rolname='{role}'"],
        capture_output=True, text=True).stdout.strip()
    if not o.startswith('SCRAM-SHA-256'):
        print(f'{role:<16} 롤 없음 또는 조회 권한 없음'); continue
    if not p:
        print(f'{role:<16} .env에 값 없음'); continue
    _, r = o.split('$', 1); its, keys = r.split('$', 1); it, sb = its.split(':')
    ok = sk(p, base64.b64decode(sb), int(it)) == keys.split(':')[0]
    print(f'{role:<16} {"OK" if ok else "MISMATCH"}')
PY
```

전부 `OK`여야 한다. `MISMATCH`가 있으면 `.env` 값으로 DB를 맞춘다:

```bash
set -a && source .env && set +a
psql -d portfolio -c "ALTER ROLE supervisor_svc PASSWORD '$SUPERVISOR_SVC_PASSWORD';"
```

`agent_svc`/`app_svc`/`portfolio_admin`은 `.env`의 DSN에서 비밀번호를 꺼내 같은 방식으로 맞춘다.

---

## 왜 psql과 Alembic 두 가지를 쓰나

관리 대상이 겹치지 않는다.

```
Alembic    service 스키마만        모델(app/domains/*/model.py)이 정본
psql       mart / anonymized / automation  SQL이 정본
```

`service` 테이블은 SQLAlchemy 모델에서 파생되므로 Alembic이 맞고,
`mart`/`anonymized`은 트리거·CHECK·COMMENT·GRANT가 대부분이라 Alembic으로는 전부
`op.execute("...")` 문자열이 된다. 그래서 SQL 파일로 둔다.

예외는 **GRANT**다. `service` 테이블은 Alembic이 만들지만 권한(`V006`/`V010`)은
SQL 파일이 건다. 권한은 개인정보 경계라 DB 담당이 관리한다.
→ **그래서 항상 `alembic upgrade head`가 먼저다.**
