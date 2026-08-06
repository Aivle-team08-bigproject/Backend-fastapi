"""Prepare the isolated PostgreSQL schema before running Alembic."""

import os

import psycopg


def main() -> None:
    database_url = os.environ["PORTFOLIO_MIGRATION_DATABASE_URL"].replace(
        "postgresql+psycopg://", "postgresql://", 1
    )
    with psycopg.connect(database_url) as connection:
        for role in ("app_svc", "agent_svc", "portfolio_admin"):
            connection.execute(
                "DO $$ BEGIN CREATE ROLE \"" + role + "\"; EXCEPTION WHEN duplicate_object THEN NULL; END $$;"
            )
        connection.execute("CREATE SCHEMA IF NOT EXISTS service")
        connection.commit()


if __name__ == "__main__":
    main()
