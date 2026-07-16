from sqlalchemy import select

from app.core import security
from app.core.config import settings
from app.common.time_utils import utcnow
from app.db.session import AsyncSessionLocal
from app.domains.employees.model.employee_model import (
    Employee,
    EmployeePermission,
    EmployeeStatus,
    PermissionCode,
)


async def ensure_bootstrap_admin() -> None:
    """관리자 계정이 하나도 없으면 기본 ADMIN 계정을 만든다.

    직원 계정은 ADMIN만 생성할 수 있으므로, 최초 진입점이 필요하다.
    운영 배포 시에는 BOOTSTRAP_ADMIN_PASSWORD 환경변수로 반드시 강한 비밀번호를 지정할 것.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Employee.id).where(Employee.employee_code == settings.bootstrap_admin_id)
        )
        if result.scalar_one_or_none() is not None:
            return

        now = utcnow()
        admin = Employee(
            employee_code=settings.bootstrap_admin_id,
            name=settings.bootstrap_admin_name,
            department=settings.bootstrap_admin_department,
            password_hash=security.hash_password(settings.bootstrap_admin_password),
            status=EmployeeStatus.ACTIVE,
            must_change_password=True,
            failed_login_count=0,
            auth_version=1,
            created_by="SYSTEM",
            created_at=now,
            updated_at=now,
        )
        admin.permissions = [EmployeePermission(permission_code=code) for code in PermissionCode]

        db.add(admin)
        await db.commit()
