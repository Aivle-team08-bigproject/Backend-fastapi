# DB 전환 안내 — 로컬 PostgreSQL → 공용 Neon

> 2026-07-29. **이제 DB를 각자 만들지 않습니다.** 팀이 같은 PostgreSQL(Neon)을 봅니다.
> 마이그레이션·CSV 적재·익명화 배치는 더 이상 필요 없습니다.

---

## 무엇이 바뀌나

| | 이전 | 이후 |
|---|---|---|
| PostgreSQL | 각자 로컬(docker `db`) | **공용 Neon 1개** |
| 마이그레이션 | 각자 `V007`~`V012` 적용 | **불필요** (적용 완료) |
| seed CSV | 따로 받아서 적재 | **불필요** (적재 완료) |
| 익명화 배치 | 각자 실행 | **불필요** (실행 완료) |
| Redis | 각자 로컬 | **각자 로컬 그대로** |

**Redis는 계속 각자 띄웁니다.** 큐와 이벤트는 공유하면 안 됩니다 — 남의 워커가 내 작업을 집어가거나, 남의 SSE 이벤트가 내 화면에 뜹니다.

```
        ┌──────── 공용 (Neon) ────────┐
        │  mart / anonymized /        │
        │  service / automation       │
        └──▲──────────▲──────────▲────┘
           │          │          │
      ┌────┴───┐ ┌────┴───┐ ┌────┴───┐
      │ 각자   │ │ 각자   │ │ 각자   │
      │FastAPI │ │FastAPI │ │FastAPI │
      │Worker  │ │Worker  │ │Worker  │
      │Redis ● │ │Redis ● │ │Redis ● │
      └────────┘ └────────┘ └────────┘
```

---

## 설정 (5분)

### 1. 코드 받기

```bash
git merge origin/db_test
```

`guns`·`juns` 브랜치는 **fast-forward라 충돌이 없습니다.**

```bash
pip install -r requirements.txt
pip install -r automation-supervisor-api/requirements.txt
```

> Supervisor가 **Celery → arq**로 바뀌었습니다. 설치가 필요합니다.

### 2. `.env` 교체

**노션에서 새 `.env`를 받아 기존 파일을 통째로 교체하세요.** DSN이 Neon을 가리킵니다.

```bash
PORTFOLIO_APP_DATABASE_URL=postgresql+psycopg://app_svc:...@ep-....neon.tech/portfolio?sslmode=require
PORTFOLIO_AGENT_DATABASE_URL=postgresql+psycopg://agent_svc:...@ep-....neon.tech/portfolio?sslmode=require
DATABASE_URL=postgresql+psycopg://supervisor_svc:...@ep-....neon.tech/portfolio?sslmode=require
```

`?sslmode=require`가 반드시 붙어 있어야 합니다. Neon은 SSL 필수입니다.

> `PORTFOLIO_MIGRATION_DATABASE_URL`(portfolio_admin)은 배포하지 않습니다. 스키마 변경은
> DB 담당(도원)이 맡습니다 — [팀 규칙](#팀-규칙) 참고.

### 3. Redis만 띄우기

```bash
docker compose up -d redis
```

**`db` 서비스는 띄우지 않습니다.** 로컬 PostgreSQL은 더 이상 쓰지 않습니다.

Docker가 없으면 Homebrew로도 됩니다:

```bash
brew install redis && brew services start redis
```

### 4. 실행

```bash
uvicorn app.main:app --reload
```

```bash
celery -A app.worker.celery_app:celery_app worker --loglevel=INFO --concurrency=2
```

```bash
python -m app.worker.status_subscriber
```

Supervisor를 쓰려면 (**Celery가 아니라 arq**입니다):

```bash
cd automation-supervisor-api && python run.py
```

```bash
cd automation-supervisor-api && arq app.workers.settings.WorkerSettings
```

---

## 확인

접속과 데이터가 정상인지 한 번에 봅니다.

```bash
python3 - <<'PY'
import os, urllib.parse, subprocess
env = {}
for line in open('.env', encoding='utf-8', errors='replace'):
    line = line.strip()
    if line and not line.startswith('#') and '=' in line:
        k, v = line.split('=', 1); env[k.strip()] = v.strip()

dsn = env['PORTFOLIO_AGENT_DATABASE_URL'].replace('+psycopg', '')
sql = """
select 'anonymized 고객/거래: '||(select count(*) from anonymized.customers)||' / '
       ||(select count(*) from anonymized.transactions)
union all select 'mart 접근(차단이 정상): '||
       case when has_schema_privilege('mart','USAGE') then 'f 아님 - 이상' else '차단됨' end
"""
print(subprocess.run(['psql', dsn, '-At', '-c', sql],
                     capture_output=True, text=True).stdout or '접속 실패')
PY
```

기대값:

```
anonymized 고객/거래: 800 / 12477
mart 접근(차단이 정상): 차단됨
```

`mart`가 차단되는 게 **정상**입니다. 에이전트 계정은 익명 계층만 봅니다.

---

## 팀 규칙

### 1. 스키마 변경은 DB 담당이 합니다

테이블·컬럼이 필요하면 **요청**해 주세요. `portfolio_admin` 권한이 없으면 `alembic upgrade head`도 실행되지 않습니다. 이건 의도된 것입니다 — 공용 DB에서 각자 마이그레이션을 돌리면 서로를 깨뜨립니다.

### 2. 익명화 배치는 절대 직접 실행하지 마세요

```bash
python scripts/anon_batch/fill_anon.py    # ← 실행 금지
```

`anonymized` 전체를 비우고 다시 채웁니다. **다른 사람이 조회 중인 데이터가 사라집니다.**
(권한상 막혀 있지만, 로컬 DB에 붙은 상태로 실행하는 실수는 가능합니다)

### 3. `service` 스키마는 공유 공간입니다

`data_requests` / `pipeline_runs` 등이 팀원 간에 섞입니다. 각자 다른 직원 계정으로 로그인해 `owner_id`로 구분하시면 편합니다. **남의 요청 데이터를 지우지 마세요.**

### 4. 스키마가 바뀌면 공지합니다

공용 DB라 변경이 즉시 전원에게 반영됩니다. DB 담당이 적용 전 공지합니다.

---

## 이번에 함께 바뀐 것

### `anon` 스키마 → `anonymized`

`anon`은 PostgreSQL Anonymizer 확장의 표준 스키마명이라 Neon이 예약어처럼 보호합니다.
아래 에러로 스키마 구성이 막혀서 이름을 바꿨습니다.

```
ERROR: cannot create trigger on relation "transactions":
       triggers on the anon schema are not permitted
```

**코드에서 `anon.` 을 직접 참조하던 곳이 있으면 `anonymized.` 로 바꿔야 합니다.**
`agent_runtime/query/registry.py`의 데이터셋 논리명(`anon_customers` 등)은 그대로입니다 — 스키마명이 아니라 화이트리스트 레이블입니다.

### `automation` 스키마 신설

Supervisor 테이블(`automation_jobs` / `automation_stage_runs` / `stage_artifact_caches`)이
`public`이 아니라 `automation` 스키마에 있습니다. `create_all()`은 제거됐고 마이그레이션이 소유합니다.

> **`public`에 이 테이블들이 있으면** 예전에 `create_all`이 실행된 흔적입니다. 로컬 DB라면 지우셔도 됩니다.

### `agent_svc` 권한 축소

`service.data_requests` / `service.clients`를 **컬럼 단위**로만 읽습니다.
`sample_email` · `contact_email`(실제 연락처)은 차단됐습니다.

⚠️ 이 두 테이블을 `agent_svc`로 조회하는 코드는 **`SELECT *` 가 실패합니다.** 컬럼을 명시하거나 ORM에서 `load_only()`를 쓰세요.

---

## 자주 나오는 문제

### `server does not support SSL, but SSL was required`

`PGSSLMODE=require`가 셸에 남아 있는데 로컬 DB에 붙으려 한 경우입니다.

```bash
unset PGSSLMODE
```

### `password authentication failed`

노션의 `.env`를 그대로 쓰셨는지 확인하세요. Neon은 로컬과 달리 **비밀번호를 실제로 검증**합니다.

### `SSL error: bad length` / `connection is lost` (대량 처리 중)

원격 DB에 한 번에 너무 많은 행을 넣을 때 발생합니다. 배치 삽입은 청크로 나누세요(`fill_anon.py`의 `INSERT_BATCH_SIZE` 참고).

### `relation "anon.customers" does not exist`

`anonymized.customers` 입니다. 위 [스키마 개명](#anon-스키마--anonymized) 참고.

### `No module named 'arq'`

```bash
pip install -r automation-supervisor-api/requirements.txt
```

### `No module named 'agent_runtime'` (supervisor 단독 실행)

`agent_runtime`은 저장소 루트에 있습니다. Supervisor를 단독으로 띄울 때는 루트가 경로에 있어야 합니다.

```bash
PYTHONPATH=..:. python run.py
```

---

## 부록 — 로컬 PostgreSQL을 계속 쓰려면

공용 DB가 느리거나 오프라인 작업이 필요하면 로컬을 병행할 수 있습니다. `.env`의 DSN만 되돌리면 됩니다.

### 처음 구축

```bash
docker compose up -d db
./sqlfiles/bootstrap.sh --with-v001 --create-roles --with-seed
```

`V001`~`V012` + `alembic upgrade head` + seed 적재를 순서대로 실행합니다.
`.env`에 롤별 비밀번호(`AGENT_SVC_PASSWORD` 등 4개)가 있어야 하고, **seed CSV는 저장소에 없으므로 따로 받아야 합니다**(`.gitignore` 대상).

익명화 데이터가 필요하면:

```bash
python scripts/anon_batch/fill_anon.py
```

`ANON_HASH_SALT`가 팀과 같아야 식별자가 일치합니다.

### 이미 로컬 DB가 있는 경우

`V007` 이후가 빠져 있을 수 있습니다. 적용 상태 확인:

```bash
psql -d portfolio -At -c "
select
  to_regclass('mart.anonymization_log') is not null                              as \"V007\",
  (select count(*) from pg_description d
     join pg_class c on c.oid = d.objoid
     join pg_namespace n on n.oid = c.relnamespace
    where n.nspname = 'anonymized') > 40                                          as \"V008\",
  to_regnamespace('automation') is not null                                       as \"V009\",
  not has_column_privilege('agent_svc','service.data_requests','sample_email','SELECT') as \"V010\",
  not exists(select 1 from pg_constraint where conname='ck_automation_jobs_status')     as \"V011\",
  to_regnamespace('anon') is null                                                 as \"V012\"
"
```

`f`인 것만 순서대로 적용합니다. **`alembic upgrade head`를 먼저** 하세요 — `V010`이 Alembic이 만든 테이블에 권한을 걸기 때문입니다.

```bash
psql -d portfolio -c "CREATE ROLE supervisor_svc LOGIN PASSWORD '<.env의 SUPERVISOR_SVC_PASSWORD>';"
```

```bash
for f in V007__anonymization_log V008__anon_column_comments V009__automation_schema \
         V010__agent_svc_column_grants V011__automation_relax_status_checks V012__rename_anon_schema; do
  psql -v ON_ERROR_STOP=1 -U portfolio_admin -d portfolio -f sqlfiles/migrations/$f.sql
done
```

### 비밀번호 정합성 확인

로컬 `pg_hba`가 `trust`면 **비밀번호가 틀려도 접속됩니다.** 그래서 `.env`와 DB가 어긋난 걸 모르고 지내다가 docker나 배포에서 한꺼번에 실패합니다.

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
pw = lambda k: urllib.parse.unquote(urllib.parse.urlparse(env.get(k, '')).password or '')
for role, p in [('agent_svc', pw('PORTFOLIO_AGENT_DATABASE_URL')),
                ('app_svc', pw('PORTFOLIO_APP_DATABASE_URL')),
                ('supervisor_svc', env.get('SUPERVISOR_SVC_PASSWORD', ''))]:
    o = subprocess.run(['psql','-At','-d','portfolio','-c',
        f"select rolpassword from pg_authid where rolname='{role}'"],
        capture_output=True, text=True).stdout.strip()
    if not o.startswith('SCRAM'): print(f'{role:<16} 롤 없음'); continue
    if not p: print(f'{role:<16} .env에 값 없음'); continue
    _, r = o.split('$', 1); its, keys = r.split('$', 1); it, sb = its.split(':')
    print(f'{role:<16} {"OK" if sk(p, base64.b64decode(sb), int(it)) == keys.split(":")[0] else "MISMATCH"}')
PY
```

`MISMATCH`가 있으면 `.env` 값으로 DB를 맞춥니다:

```bash
psql -d portfolio -c "ALTER ROLE app_svc PASSWORD '<.env의 값>';"
```

---

## 왜 psql과 Alembic 두 가지를 쓰나

관리 대상이 겹치지 않습니다.

```
Alembic    service 스키마만            모델(app/domains/*/model.py)이 정본
psql       mart / anonymized /         SQL이 정본
           automation
```

`service` 테이블은 SQLAlchemy 모델에서 파생되므로 Alembic이 맞고, 나머지는 트리거·CHECK·COMMENT·GRANT가 대부분이라 Alembic으로는 전부 `op.execute("...")` 문자열이 됩니다.

예외는 **GRANT**입니다. `service` 테이블은 Alembic이 만들지만 권한(`V006`/`V010`)은 SQL 파일이 겁니다 — 권한은 개인정보 경계라 DB 담당이 관리합니다.
→ **그래서 항상 `alembic upgrade head`가 먼저입니다.**
