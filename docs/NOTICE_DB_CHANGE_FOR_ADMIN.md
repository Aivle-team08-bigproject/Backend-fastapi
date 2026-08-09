# 공지사항 DB 변경 전달 문서

## 적용 대상

- PostgreSQL/Neon 스키마: `service`
- 신규 테이블: `service.notices`
- 적용 SQL: `docs/db-admin/20260809_create_service_notices.sql`
- 적용 주체: Production DB 관리자 (`hanacard_admin`)

## 변경 내용

| 컬럼 | 타입 | NULL | 설명 |
|---|---|---:|---|
| `id` | `BIGINT` | 불가 | PK, autoincrement |
| `title` | `VARCHAR(200)` | 불가 | 공지 제목 |
| `content` | `TEXT` | 불가 | 공지 본문 |
| `status` | `VARCHAR(20)` | 불가 | `DRAFT`, `PUBLISHED`, `ARCHIVED` |
| `created_by_employee_id` | `BIGINT` | 불가 | `service.employees.id` FK |
| `updated_by_employee_id` | `BIGINT` | 불가 | `service.employees.id` FK |
| `published_at` | `TIMESTAMPTZ` | 가능 | 게시 시각 |
| `created_at` | `TIMESTAMPTZ` | 불가 | 생성 시각(UTC) |
| `updated_at` | `TIMESTAMPTZ` | 불가 | 수정 시각(UTC) |

상태 컬럼에는 `ck_notices_status` 체크 제약조건이 적용됩니다. 다음 인덱스를 추가합니다: `ix_notices_created_by_employee_id`, `ix_notices_updated_by_employee_id`, `ix_notices_status_published_at_id(status, published_at, id)`.

테이블과 시퀀스 소유자는 `hanacard_admin`으로 설정한다. Backend 실행 계정 `app_svc`에는 테이블 `SELECT`, `INSERT`, `UPDATE`, `DELETE`와 시퀀스 `USAGE`, `SELECT` 권한을 부여한다.

## 적용 절차

1. Production DB에 `hanacard_admin`으로 접속한다.
2. `docs/db-admin/20260809_create_service_notices.sql`을 트랜잭션 단위로 실행한다.
3. 아래 검증 쿼리로 테이블·제약조건·인덱스·권한을 확인한다.

SQL은 `service.notices`가 이미 있으면 실패하도록 작성되어 있다. 실패 시 기존 테이블을 삭제하지 말고 현재 스키마와 SQL을 비교한다.

```sql
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'service' AND table_name = 'notices'
ORDER BY ordinal_position;

SELECT indexname
FROM pg_indexes
WHERE schemaname = 'service' AND tablename = 'notices';

SELECT grantee, privilege_type
FROM information_schema.role_table_grants
WHERE table_schema = 'service' AND table_name = 'notices'
  AND grantee = 'app_svc'
ORDER BY privilege_type;
```

Rollback은 공지 데이터가 없고 운영 중단 승인이 있을 때만 DB 관리자가 `DROP TABLE service.notices;`로 수행한다.
