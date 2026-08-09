# 공지사항 DB 변경 전달 문서

## 적용 대상

- PostgreSQL/Neon 스키마: `service`
- 신규 테이블: `service.notices`
- Alembic migration: `alembic/versions/e4a1b2c3d4e5_add_notices.py`
- 적용 순서: `3f8e1c2a7b90` → `e4a1b2c3d4e5`

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

## 적용 및 롤백

```bash
alembic upgrade e4a1b2c3d4e5
alembic downgrade 3f8e1c2a7b90
```

운영 적용 전 Neon 대상 브랜치에서 migration을 실행하고 아래 검증 쿼리로 테이블·제약조건·인덱스를 확인합니다.

```sql
SELECT column_name, data_type, is_nullable
FROM information_schema.columns
WHERE table_schema = 'service' AND table_name = 'notices'
ORDER BY ordinal_position;

SELECT indexname
FROM pg_indexes
WHERE schemaname = 'service' AND tablename = 'notices';
```

실제 DDL 변경은 수동 SQL이 아니라 저장소의 Alembic migration을 기준으로 적용합니다.
