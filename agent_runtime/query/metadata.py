from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from agent_runtime.query.registry import DATASETS, canonical_dataset


_LOGICAL_NAME_BY_TABLE = {
    dataset.table.name: logical_name
    for logical_name, dataset in DATASETS.items()
}


async def load_dataset_metadata(
    session: AsyncSession,
    available_data: list[str] | None = None,
) -> list[dict]:
    """Neon의 테이블·컬럼 타입과 COMMENT를 선별 Agent 입력 형태로 반환한다."""
    requested_order = [
        canonical_dataset(name) for name in (available_data or DATASETS)
    ]
    requested = set(requested_order)
    rows = (
        await session.execute(
            text(
                """
                SELECT
                    cls.relname AS table_name,
                    obj_description(cls.oid, 'pg_class') AS table_comment,
                    att.attname AS column_name,
                    format_type(att.atttypid, att.atttypmod) AS data_type,
                    col_description(cls.oid, att.attnum) AS column_comment
                FROM pg_class AS cls
                JOIN pg_namespace AS ns ON ns.oid = cls.relnamespace
                JOIN pg_attribute AS att ON att.attrelid = cls.oid
                WHERE ns.nspname = 'anonymized'
                  AND cls.relkind IN ('r', 'p')
                  AND att.attnum > 0
                  AND NOT att.attisdropped
                ORDER BY cls.relname, att.attnum
                """
            )
        )
    ).mappings().all()

    grouped: dict[str, dict] = {}
    for row in rows:
        logical_name = _LOGICAL_NAME_BY_TABLE.get(row["table_name"])
        if logical_name is None or logical_name not in requested:
            continue
        dataset = DATASETS[logical_name]
        if row["column_name"] in dataset.blocked_columns:
            continue
        item = grouped.setdefault(
            logical_name,
            {
                "dataset": logical_name,
                "schema": "anonymized",
                "table": row["table_name"],
                "comment": row["table_comment"] or "",
                "columns": [],
            },
        )
        item["columns"].append(
            {
                "name": row["column_name"],
                "data_type": row["data_type"],
                "comment": row["column_comment"] or "",
            }
        )
    return [grouped[name] for name in requested_order if name in grouped]


async def load_reference_catalogs(session: AsyncSession) -> list[dict]:
    """의미 카테고리를 실행 값으로 바꾸는 비개인 참조 카탈로그를 반환한다."""
    metadata_rows = (
        await session.execute(
            text(
                """
                SELECT
                    obj_description(cls.oid, 'pg_class') AS table_comment,
                    att.attname AS column_name,
                    format_type(att.atttypid, att.atttypmod) AS data_type,
                    col_description(cls.oid, att.attnum) AS column_comment
                FROM pg_class AS cls
                JOIN pg_namespace AS ns ON ns.oid = cls.relnamespace
                JOIN pg_attribute AS att ON att.attrelid = cls.oid
                WHERE ns.nspname = 'anonymized'
                  AND cls.relname = 'mcc_codes'
                  AND cls.relkind IN ('r', 'p')
                  AND att.attnum > 0
                  AND NOT att.attisdropped
                ORDER BY att.attnum
                """
            )
        )
    ).mappings().all()
    if not metadata_rows:
        return []

    entries = (
        await session.execute(
            text(
                """
                SELECT mcc_code, mcc_name
                FROM anonymized.mcc_codes
                ORDER BY mcc_code
                """
            )
        )
    ).mappings().all()
    return [
        {
            "catalog": "mcc_codes",
            "schema": "anonymized",
            "table": "mcc_codes",
            "comment": metadata_rows[0]["table_comment"] or "",
            "columns": [
                {
                    "name": row["column_name"],
                    "data_type": row["data_type"],
                    "comment": row["column_comment"] or "",
                }
                for row in metadata_rows
            ],
            "target_columns": [
                {"dataset": "transaction_pseudonymized", "column": "mcc_code"},
                {"dataset": "merchant", "column": "mcc_code"},
            ],
            "entries": [dict(row) for row in entries],
        }
    ]
