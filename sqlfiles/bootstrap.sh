#!/usr/bin/env bash
# =====================================================================
# portfolio DB 부트스트랩 — mart/anonymized(SQL) + service(Alembic)를 순서대로 구축
#
# 전제:
#   1) portfolio 데이터베이스가 이미 생성돼 있을 것 (docker compose가 생성)
#   2) PostgreSQL superuser 접속 정보가 설정돼 있을 것
#   3) 역할 비밀번호가 AGENT_SVC_PASSWORD / APP_SVC_PASSWORD /
#      PORTFOLIO_ADMIN_PASSWORD 환경변수에 설정돼 있을 것
#   4) 레포의 .env에 PORTFOLIO_MIGRATION_DATABASE_URL이 설정돼 있을 것 (Alembic용)
#
# 사용:
#   ./bootstrap.sh --with-v001 --create-roles # 신규 DB 전체 구축
#   ./bootstrap.sh                        # V002~V006 + alembic
#   ./bootstrap.sh --with-v001            # V001부터 (역할은 사전 생성)
#   ./bootstrap.sh --skip-alembic         # SQL만, Alembic 제외
#   ./bootstrap.sh --with-seed --with-verify # CSV 적재 + 검증
#   REPO_DIR=~/path/to/Backend-fastapi ./bootstrap.sh
#
# 왜 스크립트인가: Postgres 컨테이너의 /docker-entrypoint-initdb.d 자동 실행은
#   파일명 알파벳 순이라 V10이 V2보다 먼저 오는 등 순서가 깨진다. 실행 순서를
#   명시적으로 고정하기 위해 스크립트로 관리한다.
# =====================================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-$HERE/../.env}"
if [[ -f "$ENV_FILE" ]]; then
    # JSON 배열 환경변수는 shell source 시 따옴표가 제거되어 깨질 수 있다.
    # 명시적으로 주입한 값은 보존하고, 그 외에는 Pydantic이 dotenv 파일에서 읽게 둔다.
    CORS_WAS_SET=0
    CORS_VALUE=""
    if [[ "${CORS_ALLOWED_ORIGINS+x}" == x ]]; then
        CORS_WAS_SET=1
        CORS_VALUE="$CORS_ALLOWED_ORIGINS"
    fi
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
    if [[ "$CORS_WAS_SET" -eq 1 ]]; then
        CORS_ALLOWED_ORIGINS="$CORS_VALUE"
        export CORS_ALLOWED_ORIGINS
    else
        unset CORS_ALLOWED_ORIGINS
    fi
fi

DB="${PGDATABASE:-portfolio}"
ADMIN_USER="${ADMIN_USER:-portfolio_admin}"
SUPER_USER="${SUPER_USER:-postgres}"
SUPERUSER_PASSWORD="${SUPERUSER_PASSWORD:-${POSTGRES_PASSWORD:-}}"
REPO_DIR="${REPO_DIR:-$HERE/..}"
ALEMBIC_BIN="${ALEMBIC_BIN:-alembic}"

WITH_V001=0
SKIP_ALEMBIC=0
CREATE_ROLES=0
WITH_SEED=0
WITH_VERIFY=0
for arg in "$@"; do
    case "$arg" in
    --with-v001)    WITH_V001=1 ;;
    --skip-alembic) SKIP_ALEMBIC=1 ;;
    --create-roles) CREATE_ROLES=1 ;;
    --with-seed)    WITH_SEED=1 ;;
    --with-verify)  WITH_VERIFY=1 ;;
        *) echo "알 수 없는 옵션: $arg" >&2; exit 1 ;;
    esac
done

password_for() {  # password_for <실행계정>
    case "$1" in
        "$SUPER_USER") printf '%s' "$SUPERUSER_PASSWORD" ;;
        portfolio_admin) printf '%s' "${PORTFOLIO_ADMIN_PASSWORD:-}" ;;
        app_svc) printf '%s' "${APP_SVC_PASSWORD:-}" ;;
        agent_svc) printf '%s' "${AGENT_SVC_PASSWORD:-}" ;;
        *) printf '%s' "" ;;
    esac
}

run_sql() {  # run_sql <실행계정> <파일경로>
    local user="$1" file="$2"
    echo ""
    echo "▶ [$user] $(basename "$file")"
    PGPASSWORD="$(password_for "$user")" psql -v ON_ERROR_STOP=1 -U "$user" -d "$DB" -v DB_NAME="$DB" -f "$file"
}

create_role() {  # create_role <역할명> <비밀번호 환경변수명>
    local role="$1"
    local password_var="$2"
    local password="${!password_var:-}"
    if [[ -z "$password" ]]; then
        echo "  ✖ $password_var 환경변수가 필요하다" >&2
        exit 1
    fi
    # psql -c에서는 quoted variable substitution이 적용되지 않으므로
    # SQL literal에 맞게 작은따옴표를 escape한 뒤 전달한다.
    local escaped_password="${password//\'/\'\'}"
    echo "▶ [$SUPER_USER] 역할 확인: $role"
    if [[ "$(PGPASSWORD="$SUPERUSER_PASSWORD" psql -At -U "$SUPER_USER" -d "$DB" -c "SELECT 1 FROM pg_roles WHERE rolname = '$role'")" != "1" ]]; then
        PGPASSWORD="$SUPERUSER_PASSWORD" psql -v ON_ERROR_STOP=1 -U "$SUPER_USER" -d "$DB" \
            -c "CREATE ROLE \"$role\" LOGIN PASSWORD '$escaped_password';"
    else
        # 기존 클러스터에 NOLOGIN/stale password 상태로 남아 있어도
        # 이 브랜치의 초기화만으로 서비스 계정이 접속 가능해야 한다.
        PGPASSWORD="$SUPERUSER_PASSWORD" psql -v ON_ERROR_STOP=1 -U "$SUPER_USER" -d "$DB" \
            -c "ALTER ROLE \"$role\" LOGIN PASSWORD '$escaped_password';"
    fi
}

echo "════════════════════════════════════════════"
echo " portfolio DB 부트스트랩"
echo "   DB=$DB  마이그레이션계정=$ADMIN_USER"
echo "════════════════════════════════════════════"

# --- 1. 기본 설정 (superuser) -----------------------------------------
if [[ "$CREATE_ROLES" -eq 1 ]]; then
    create_role "agent_svc" "AGENT_SVC_PASSWORD"
    create_role "app_svc" "APP_SVC_PASSWORD"
    create_role "portfolio_admin" "PORTFOLIO_ADMIN_PASSWORD"
    create_role "supervisor_svc" "SUPERVISOR_SVC_PASSWORD"
fi

if [[ "$WITH_V001" -eq 1 ]]; then
    run_sql "$SUPER_USER" "$HERE/migrations/V001__roles_and_database.sql"
    echo "  ※ timezone 변경은 새 세션부터 적용된다"
fi

# --- 2. mart / anonymized (portfolio_admin) ----------------------------------
# 반드시 portfolio_admin으로 실행한다 — 실행 계정이 곧 테이블 소유자가 되고,
# 소유자만 ALTER/TRUNCATE/DISABLE TRIGGER를 할 수 있기 때문.
for f in \
    "$HERE/migrations/V002__mart_tables.sql" \
    "$HERE/migrations/V003__mart_foreign_keys.sql" \
    "$HERE/migrations/V004__mart_triggers.sql" \
    "$HERE/migrations/V005__anon_schema.sql"
do
    run_sql "$ADMIN_USER" "$f"
done

# --- 3. service (Alembic) ---------------------------------------------
if [[ "$SKIP_ALEMBIC" -eq 0 ]]; then
    echo ""
    echo "▶ [alembic] upgrade head   (in $REPO_DIR)"
    if [[ ! -d "$REPO_DIR" ]]; then
        echo "  ✖ 레포를 찾을 수 없다: $REPO_DIR" >&2
        echo "    REPO_DIR 환경변수로 경로를 지정하거나 --skip-alembic 을 사용할 것" >&2
        exit 1
    fi
    if ! command -v "$ALEMBIC_BIN" >/dev/null 2>&1; then
        echo "  ✖ alembic CLI를 찾을 수 없다: $ALEMBIC_BIN" >&2
        echo "    requirements.txt를 설치하거나 ALEMBIC_BIN으로 실행 파일 경로를 지정할 것" >&2
        exit 1
    fi
    ( cd "$REPO_DIR" && "$ALEMBIC_BIN" upgrade head )
fi

# --- 4. service GRANT (테이블 생성 후에만 유효) ------------------------
run_sql "$ADMIN_USER" "$HERE/migrations/V006__service_grants.sql"

# --- 4-b. 추가 마이그레이션 -------------------------------------------
# V007  mart.anonymization_log  익명처리 이력(법정 3년 보존)
# V008  anonymized 컬럼 코멘트        LLM이 읽는 스키마 설명
# V009  automation 스키마       Supervisor 실행 계층 (supervisor_svc 롤 필요)
# V010  agent_svc 권한 축소     테이블 -> 컬럼 단위. V006 다음이어야 함
for f in \
    "$HERE/migrations/V007__anonymization_log.sql" \
    "$HERE/migrations/V008__anon_column_comments.sql" \
    "$HERE/migrations/V009__automation_schema.sql" \
    "$HERE/migrations/V010__agent_svc_column_grants.sql" \
    "$HERE/migrations/V011__automation_relax_status_checks.sql"\
    "$HERE/migrations/V012__rename_anon_schema.sql"
do
    run_sql "$ADMIN_USER" "$f"
done


# --- 5. 선택 데이터 적재·검증 ------------------------------------------
if [[ "$WITH_SEED" -eq 1 ]]; then
    echo ""
    echo "▶ [$ADMIN_USER] mart seed"
    ( cd "$HERE/seed" && PGPASSWORD="$(password_for "$ADMIN_USER")" psql -v ON_ERROR_STOP=1 -U "$ADMIN_USER" -d "$DB" -v DB_NAME="$DB" -f load_csv.sql )
fi

if [[ "$WITH_VERIFY" -eq 1 ]]; then
    run_sql "$ADMIN_USER" "$HERE/verify/validation_report.sql"
fi

# --- 완료 -------------------------------------------------------------
cat <<'EOF'

════════════════════════════════════════════
 ✅ 구축 완료
════════════════════════════════════════════
남은 선택 단계:
  데이터 적재 : ./sqlfiles/bootstrap.sh --with-seed
  검증 리포트 : ./sqlfiles/bootstrap.sh --with-verify
  anonymized 채우기 : 현재 번들에는 구조·권한만 포함되어 있음 (별도 배치 필요)
EOF
