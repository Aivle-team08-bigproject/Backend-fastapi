#!/usr/bin/env bash
set -euo pipefail

# Apply every Alembic branch head to a database whose URL is stored in Secrets
# Manager. The secret may be a plain URL or JSON with url/connection_string/value.
# The URL is never printed or written to disk.

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: $0 <database-secret-arn> [aws-profile]" >&2
  exit 2
fi

SECRET_ARN="$1"
AWS_PROFILE_NAME="${2:-terraform-session}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"

SECRET_STRING="$(aws secretsmanager get-secret-value \
  --profile "${AWS_PROFILE_NAME}" \
  --region ap-northeast-2 \
  --secret-id "${SECRET_ARN}" \
  --query SecretString \
  --output text)"

DB_URL="$(printf '%s' "${SECRET_STRING}" | python3 -c '
import json, sys
raw = sys.stdin.read().strip()
try:
    value = json.loads(raw)
except json.JSONDecodeError:
    value = raw
if isinstance(value, dict):
    for key in ("url", "connection_string", "value"):
        if isinstance(value.get(key), str) and value[key].strip():
            value = value[key].strip()
            break
    else:
        strings = [item.strip() for item in value.values() if isinstance(item, str) and item.strip()]
        if len(strings) != 1:
            raise SystemExit("database secret JSON must have url, connection_string, value, or one string value")
        value = strings[0]
if not isinstance(value, str) or not value.startswith(("postgresql://", "postgres://", "postgresql+psycopg://")):
    raise SystemExit("database secret is not a PostgreSQL URL")
if value.startswith("postgresql://"):
    value = "postgresql+psycopg://" + value.removeprefix("postgresql://")
elif value.startswith("postgres://"):
    value = "postgresql+psycopg://" + value.removeprefix("postgres://")
print(value, end="")
')"
unset SECRET_STRING

export APP_ENV=dev
export HANACARD_MIGRATION_DATABASE_URL="${DB_URL}"
unset DB_URL

cd "${PROJECT_DIR}"
if [[ -x "${PROJECT_DIR}/.venv/bin/alembic" ]]; then
  ALEMBIC_BIN="${PROJECT_DIR}/.venv/bin/alembic"
elif command -v alembic >/dev/null 2>&1; then
  ALEMBIC_BIN="$(command -v alembic)"
else
  echo "alembic is not installed; create .venv and install requirements.txt" >&2
  exit 1
fi

"${ALEMBIC_BIN}" heads
"${ALEMBIC_BIN}" upgrade heads
"${ALEMBIC_BIN}" current

unset HANACARD_MIGRATION_DATABASE_URL
