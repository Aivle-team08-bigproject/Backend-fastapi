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
from app.db.session import AsyncSessionLocal, engine
from app.domains.employees.model import (
    Department,
    Employee,
    EmployeePermission,
    EmployeeStatus,
    PermissionCode,
)


DEMO_EMPLOYEE_CODE = "DEMO-001"
DEMO_EMPLOYEE_NAME = "홍길동 책임"
DEMO_EMPLOYEE_EMAIL = "demo.001@company.com"
DEMO_DEPARTMENT = "데이터 운영팀"
# 회원 관리·권한 변경 UI까지 로컬에서 검증할 수 있도록 데모 계정은 관리자 프로필을 사용한다.
DEMO_PERMISSIONS = tuple(PermissionCode)


async def seed_demo_user() -> tuple[str, str]:
    password = generate_temporary_password()
    now = utcnow()

    async with AsyncSessionLocal() as session:
        department_id = await session.scalar(select(Department.id).where(Department.name == DEMO_DEPARTMENT))
        if department_id is None:
            department = Department(
                name=DEMO_DEPARTMENT,
                code=f"DEPT-{DEMO_DEPARTMENT}",
                is_active=True,
                created_at=now,
                updated_at=now,
            )
            session.add(department)
            await session.flush()
            department_id = department.id

        employee = await session.scalar(
            select(Employee)
            .options(selectinload(Employee.permissions))
            .where(Employee.employee_code == DEMO_EMPLOYEE_CODE)
        )

        if employee is None:
            employee = Employee(
                employee_code=DEMO_EMPLOYEE_CODE,
                name=DEMO_EMPLOYEE_NAME,
                email=DEMO_EMPLOYEE_EMAIL,
                department_id=department_id,
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
            employee.name = DEMO_EMPLOYEE_NAME
            employee.department_id = department_id
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
