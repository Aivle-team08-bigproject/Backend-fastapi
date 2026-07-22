#!/bin/bash
# requirements/tasks 도메인은 별도 DB(REQUIREMENTS_DB_NAME)를 쓴다 (app/db/legacy_session.py 참고).
# POSTGRES_DB는 컨테이너가 기본으로 하나만 만들어주므로, 두 번째 DB는 여기서 직접 생성한다.
set -e

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-EOSQL
    SELECT 'CREATE DATABASE "$REQUIREMENTS_DB_NAME" OWNER "$POSTGRES_USER"'
    WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = '$REQUIREMENTS_DB_NAME')\gexec
EOSQL
