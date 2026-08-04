"""Create the deterministic administrator used by the isolated Docker test DB."""

import asyncio

from sqlalchemy import delete, select

from app.common.time_utils import utcnow
from app.core.security import hash_password
from app.db.session import AsyncSessionLocal, engine
from app.domains.employees.model import (
    Department,
    Employee,
    EmployeePermission,
    EmployeeRole,
    EmployeeStatus,
    PermissionCode,
)


EMAIL = "admin@company.com"
PASSWORD = "TestAdmin!2026Secure"


async def seed() -> None:
    now = utcnow()
    async with AsyncSessionLocal() as db:
        department = await db.scalar(select(Department).where(Department.code == "DEPT-데이터사업팀"))
        if department is None:
            department = await db.scalar(select(Department).where(Department.name == "데이터사업팀"))
        if department is None:
            raise RuntimeError("migration did not create the test department")

        employee = await db.scalar(select(Employee).where(Employee.email == EMAIL))
        if employee is None:
            employee = Employee(
                employee_code="HANA-ADMIN-001",
                name="테스트 관리자",
                email=EMAIL,
                department_id=department.id,
                password_hash=hash_password(PASSWORD),
                status=EmployeeStatus.ACTIVE,
                role_code=EmployeeRole.ADMIN.value,
                must_change_password=False,
                failed_login_count=0,
                auth_version=1,
                created_by="TEST_SEED",
                created_at=now,
                updated_at=now,
            )
            db.add(employee)
        else:
            employee.password_hash = hash_password(PASSWORD)
            employee.status = EmployeeStatus.ACTIVE
            employee.role_code = EmployeeRole.ADMIN.value
            employee.must_change_password = False
            employee.auth_version += 1
            employee.updated_at = now
        await db.flush()
        await db.execute(delete(EmployeePermission).where(EmployeePermission.employee_id == employee.id))
        db.add_all([
            EmployeePermission(employee_id=employee.id, permission_code=permission)
            for permission in PermissionCode
        ])
        await db.commit()
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(seed())
