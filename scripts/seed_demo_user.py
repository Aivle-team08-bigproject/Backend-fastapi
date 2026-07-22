"""로컬 통합 테스트용 직원 계정을 생성하거나 초기화한다.

실행:
    python -m scripts.seed_demo_user

비밀번호는 실행할 때마다 새로 생성하며 DB에는 BCrypt 해시만 저장한다.
"""

import asyncio

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.common.time_utils import utcnow
from app.core.security import generate_temporary_password, hash_password, verify_password
from app.db.session import AsyncSessionLocal, engine, init_db
from app.domains.employees.model.employee_model import (
    Employee,
    EmployeePermission,
    EmployeeStatus,
    PermissionCode,
)


DEMO_EMPLOYEE_CODE = "DEMO-001"
DEMO_PERMISSIONS = (
    PermissionCode.DATA_PRODUCT_READ,
    PermissionCode.DATA_PRODUCT_WRITE,
    PermissionCode.QUOTE_READ,
    PermissionCode.QUOTE_PROCESS,
    PermissionCode.CONTRACT_MANAGE,
)


async def seed_demo_user() -> tuple[str, str]:
    await init_db()
    password = generate_temporary_password()
    now = utcnow()

    async with AsyncSessionLocal() as session:
        employee = await session.scalar(
            select(Employee)
            .options(selectinload(Employee.permissions))
            .where(Employee.employee_code == DEMO_EMPLOYEE_CODE)
        )

        if employee is None:
            employee = Employee(
                employee_code=DEMO_EMPLOYEE_CODE,
                name="데모 사용자",
                department="데모 운영팀",
                password_hash=hash_password(password),
                status=EmployeeStatus.ACTIVE,
                must_change_password=False,
                failed_login_count=0,
                locked_until=None,
                auth_version=1,
                created_by="LOCAL_DEMO_SEED",
                created_at=now,
                updated_at=now,
            )
            employee.permissions = [
                EmployeePermission(permission_code=permission) for permission in DEMO_PERMISSIONS
            ]
            session.add(employee)
        else:
            employee.name = "데모 사용자"
            employee.department = "데모 운영팀"
            employee.password_hash = hash_password(password)
            employee.status = EmployeeStatus.ACTIVE
            employee.must_change_password = False
            employee.failed_login_count = 0
            employee.locked_until = None
            employee.auth_version += 1
            employee.updated_at = now
            employee.permissions = [
                EmployeePermission(permission_code=permission) for permission in DEMO_PERMISSIONS
            ]

        await session.commit()
        assert verify_password(password, employee.password_hash)

    return DEMO_EMPLOYEE_CODE, password


async def main() -> None:
    try:
        employee_code, password = await seed_demo_user()
        print(f"employee_code={employee_code}")
        print(f"password={password}")
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
