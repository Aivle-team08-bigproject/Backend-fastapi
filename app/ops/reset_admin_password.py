"""운영자 전용 관리자 비밀번호 복구 명령.

이 모듈은 FastAPI startup, API 라우터, worker에서 import하지 않는다.
계정 잠금·비밀번호 분실·세션 탈취 의심 시 운영자가 SSM 등에서 명시적으로 실행한다.
"""

import argparse
import asyncio
import getpass
import json
import os
import sys

import boto3
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine


def _resolve_database_url(secret_id: str) -> str:
    raw = boto3.client(
        "secretsmanager",
        region_name=os.getenv("AWS_REGION", "ap-northeast-2"),
    ).get_secret_value(SecretId=secret_id).get("SecretString")
    if not isinstance(raw, str) or not raw.strip():
        raise RuntimeError("데이터베이스 secret이 비어 있습니다.")

    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        value = raw.strip()

    if isinstance(value, dict):
        for key in ("url", "connection_string", "value"):
            candidate = value.get(key)
            if isinstance(candidate, str) and candidate.strip():
                value = candidate.strip()
                break
        else:
            strings = [item.strip() for item in value.values() if isinstance(item, str) and item.strip()]
            if len(strings) != 1:
                raise RuntimeError("database secret JSON에 url, connection_string 또는 value가 필요합니다.")
            value = strings[0]

    if not isinstance(value, str) or not value.startswith(("postgresql://", "postgres://", "postgresql+psycopg://")):
        raise RuntimeError("database secret이 PostgreSQL connection string이 아닙니다.")
    if value.startswith("postgresql://"):
        value = "postgresql+psycopg://" + value.removeprefix("postgresql://")
    elif value.startswith("postgres://"):
        value = "postgresql+psycopg://" + value.removeprefix("postgres://")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="기존 관리자 계정의 비밀번호를 운영자 절차로 초기화합니다."
    )
    parser.add_argument("--employee-code", required=True, help="비밀번호를 초기화할 관리자 직원 코드")
    parser.add_argument(
        "--database-secret-id",
        help="Secrets Manager의 DB secret 이름 또는 ARN (url/connection_string/value/plain string 지원)",
    )
    return parser


def _read_password() -> tuple[str, bool]:
    from app.core import security

    password = getpass.getpass("새 초기 비밀번호(비워두면 임시 비밀번호 생성): ")
    if not password:
        return security.generate_temporary_password(), True
    confirmation = getpass.getpass("새 초기 비밀번호 확인: ")
    if password != confirmation:
        raise ValueError("비밀번호가 일치하지 않습니다.")
    return password, False


def _validate_password(password: str) -> None:
    from app.domains.auth.schema.auth_schema import validate_password_complexity

    if len(password) < 12 or len(password.encode("utf-8")) > 72:
        raise ValueError("비밀번호는 12자 이상이며 72바이트 이하여야 합니다.")
    validate_password_complexity(password)


async def _reset_admin_password(employee_code: str, password: str) -> int:
    from app.common.time_utils import utcnow
    from app.core import security
    from app.core.config import settings
    from app.domains.auth.model.session_model import LoginSession
    from app.domains.employees.model import Employee, EmployeeRole, EmployeeStatus

    engine = create_async_engine(
        settings.portfolio_migration_database_url,
        echo=False,
        future=True,
        pool_pre_ping=True,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with session_factory() as db:
            result = await db.execute(
                select(Employee)
                .where(Employee.employee_code == employee_code)
                .with_for_update()
            )
            employee = result.scalar_one_or_none()
            if employee is None:
                raise RuntimeError("관리자 계정을 찾을 수 없습니다.")
            if employee.role_code != EmployeeRole.ADMIN.value:
                raise RuntimeError("지정한 계정은 관리자 계정이 아닙니다.")
            if employee.status == EmployeeStatus.DISABLED:
                raise RuntimeError("비활성화된 관리자 계정은 별도 승인 절차 없이 복구할 수 없습니다.")

            now = utcnow()
            employee.password_hash = security.hash_password(password)
            employee.must_change_password = True
            employee.failed_login_count = 0
            employee.locked_until = None
            if employee.status == EmployeeStatus.LOCKED:
                employee.status = EmployeeStatus.ACTIVE
            employee.auth_version += 1
            employee.updated_at = now

            await db.execute(
                update(LoginSession)
                .where(
                    LoginSession.employee_id == employee.id,
                    LoginSession.revoked_at.is_(None),
                )
                .values(revoked_at=now, revoke_reason="운영자 비밀번호 초기화")
            )
            await db.commit()
            return employee.id
    finally:
        await engine.dispose()


def main() -> int:
    args = _parser().parse_args()
    try:
        if args.database_secret_id:
            os.environ["PORTFOLIO_MIGRATION_DATABASE_URL"] = _resolve_database_url(args.database_secret_id)
        password, generated = _read_password()
        _validate_password(password)
        asyncio.run(_reset_admin_password(args.employee_code, password))
    except (ValueError, RuntimeError) as exc:
        print(f"관리자 비밀번호 초기화 실패: {exc}", file=sys.stderr)
        return 1

    print("관리자 비밀번호를 초기화했습니다.")
    if generated:
        print(f"초기 임시 비밀번호(이번 출력에서만 확인): {password}")
    print("보안을 위해 다음 로그인에서 비밀번호를 변경해야 합니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
