"""기존 service DB에서 제거된 StageRun 실행기 컬럼을 정리한다.

기본값은 점검만 수행한다. 실제 컬럼 삭제는 명시적으로 `--apply`를 전달해야 한다.

실행:
    python -m scripts.migrate_remove_executor_columns
    python -m scripts.migrate_remove_executor_columns --apply
"""

import argparse
import asyncio

from sqlalchemy import text

from app.db.session import engine


async def migrate(*, apply: bool) -> None:
    async with engine.begin() as connection:
        table_exists = await connection.scalar(
            text("SELECT to_regclass('public.stage_runs') IS NOT NULL")
        )
        if not table_exists:
            print("stage_runs 테이블이 없어 변경하지 않았습니다.")
            return

        if not apply:
            print("점검 완료: stage_runs.executor/executor_reference 삭제 대기")
            print("실제 적용: python -m scripts.migrate_remove_executor_columns --apply")
            return

        await connection.execute(
            text(
                "ALTER TABLE stage_runs "
                "DROP COLUMN IF EXISTS executor, "
                "DROP COLUMN IF EXISTS executor_reference"
            )
        )
        print("stage_runs.executor/executor_reference 컬럼을 정리했습니다.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove obsolete StageRun executor columns")
    parser.add_argument("--apply", action="store_true", help="실제 컬럼 삭제를 수행")
    args = parser.parse_args()
    asyncio.run(migrate(apply=args.apply))


if __name__ == "__main__":
    main()
