"""최초 관리자 계정 프로비저닝 명령.

이 모듈은 FastAPI startup, API 라우터, worker에서 import하지 않는다.
운영자가 배포 후 SSM Session Manager 등으로 명시적으로 한 번 실행한다.
"""

import argparse
import asyncio
import getpass
import sys
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core import security
from app.core.config import settings
from app.common.time_utils import utcnow
from app.domains.auth.schema.auth_schema import validate_company_email, validate_password_complexity
from app.domains.employees.model import (
    Department,
    Employee,
    EmployeePermission,
    EmployeeRole,
    EmployeeStatus,
    PermissionCode,
)


@dataclass(frozen=True)
class ProvisionAdminInput:
    employee_code: str
    name: str
    email: str
    department_code: str
    password: str


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="최초 관리자 계정을 운영자가 명시적으로 생성합니다."
    )
    parser.add_argument("--employee-code", required=True, help="관리자 직원 코드")
    parser.add_argument("--name", required=True, help="관리자 표시 이름")
    parser.add_argument("--email", required=True, help="관리자 로그인 이메일")
    parser.add_argument("--department-code", required=True, help="기존 활성 부서 코드")
    return parser


def _read_password() -> tuple[str, bool]:
    password = getpass.getpass("초기 비밀번호(비워두면 임시 비밀번호 생성): ")
    if not password:
        return security.generate_temporary_password(), True
    confirmation = getpass.getpass("초기 비밀번호 확인: ")
    if password != confirmation:
        raise ValueError("비밀번호가 일치하지 않습니다.")
    return password, False


def _validate_input(values: ProvisionAdminInput) -> None:
    if not values.employee_code.strip() or len(values.employee_code) > 40:
        raise ValueError("직원 코드는 1~40자로 입력해야 합니다.")
    if not values.name.strip() or len(values.name) > 100:
        raise ValueError("이름은 1~100자로 입력해야 합니다.")
    validate_company_email(values.email)
    if not values.department_code.strip() or len(values.department_code) > 40:
        raise ValueError("부서 코드는 1~40자로 입력해야 합니다.")
    if len(values.password) < 12 or len(values.password.encode("utf-8")) > 72:
        raise ValueError("비밀번호는 12자 이상이며 72바이트 이하여야 합니다.")
    validate_password_complexity(values.password)


async def _provision(values: ProvisionAdminInput) -> bool:
    engine = create_async_engine(
        settings.portfolio_migration_database_url,
        echo=False,
        future=True,
        pool_pre_ping=True,
    )
    session_factory = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)
    try:
        async with session_factory() as db:
            existing_admin = await db.scalar(
                select(Employee.id).where(Employee.role_code == EmployeeRole.ADMIN.value).limit(1)
            )
            if existing_admin is not None:
                raise RuntimeError("관리자 계정이 이미 존재합니다. 프로비저닝을 중단합니다.")

            duplicate = await db.scalar(
                select(Employee.id).where(
                    (Employee.employee_code == values.employee_code)
                    | (Employee.email == values.email)
                ).limit(1)
            )
            if duplicate is not None:
                raise RuntimeError("직원 코드 또는 이메일이 이미 존재합니다.")

            department = await db.scalar(
                select(Department).where(
                    Department.code == values.department_code,
                    Department.is_active.is_(True),
                )
            )
            if department is None:
                raise RuntimeError("존재하지 않거나 비활성화된 부서 코드입니다.")

            now = utcnow()
            employee = Employee(
                employee_code=values.employee_code,
                name=values.name.strip(),
                email=values.email.strip().lower(),
                department_id=department.id,
                password_hash=security.hash_password(values.password),
                status=EmployeeStatus.ACTIVE,
                role_code=EmployeeRole.ADMIN.value,
                must_change_password=True,
                failed_login_count=0,
                auth_version=1,
                created_by="OPS_PROVISIONING",
                created_at=now,
                updated_at=now,
            )
            employee.permissions = [
                EmployeePermission(permission_code=code) for code in PermissionCode
            ]
            db.add(employee)
            try:
                await db.commit()
            except IntegrityError as exc:
                await db.rollback()
                raise RuntimeError("관리자 생성 중 중복 또는 무결성 오류가 발생했습니다.") from exc
            return True
    finally:
        await engine.dispose()


def main() -> int:
    args = _parser().parse_args()
    try:
        password, generated = _read_password()
        values = ProvisionAdminInput(
            employee_code=args.employee_code,
            name=args.name,
            email=args.email,
            department_code=args.department_code,
            password=password,
        )
        _validate_input(values)
        asyncio.run(_provision(values))
    except (ValueError, RuntimeError) as exc:
        print(f"프로비저닝 실패: {exc}", file=sys.stderr)
        return 1

    print("최초 관리자 계정을 생성했습니다.")
    if generated:
        print(f"초기 임시 비밀번호(이번 출력에서만 확인): {password}")
    print("보안을 위해 다음 로그인에서 비밀번호를 변경해야 합니다.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
