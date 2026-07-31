from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.core import security
from app.core.config import settings
from app.common.time_utils import utcnow
from app.db.session import AsyncSessionLocal
from app.domains.employees.model import (
    Department,
    Employee,
    EmployeePermission,
    EmployeeRole,
    EmployeeStatus,
    PermissionCode,
)


async def _ensure_department(db: AsyncSession, name: str) -> int:
    """이름으로 부서를 찾고, 없으면 만들어서 id를 반환한다 (부트스트랩 전용 get-or-create)."""
    result = await db.execute(select(Department.id).where(Department.name == name))
    department_id = result.scalar_one_or_none()
    if department_id is not None:
        return department_id

    now = utcnow()
    department = Department(
        name=name,
        code=f"DEPT-{name}",
        is_active=True,
        created_at=now,
        updated_at=now,
    )
    db.add(department)
    await db.flush()
    return department.id


async def ensure_bootstrap_admin() -> None:
    """관리자 계정이 하나도 없으면 기본 ADMIN 계정을 만든다.

    직원 계정은 ADMIN만 생성할 수 있으므로, 최초 진입점이 필요하다.
    운영 배포 시에는 BOOTSTRAP_ADMIN_PASSWORD 환경변수로 반드시 강한 비밀번호를 지정할 것.
    """
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Employee)
            .options(selectinload(Employee.permissions))
            .where(Employee.employee_code == settings.bootstrap_admin_id)
        )
        admin = result.scalar_one_or_none()
        department_id = await _ensure_department(db, settings.bootstrap_admin_department)
        now = utcnow()

        if admin is not None:
            # 회원가입 기능 도입 마이그레이션이 기존 계정의 email을 placeholder(@migrated.invalid)로
            # 채워 넣었는데, 로그인은 이제 email 기준이라 그대로 두면 최초 관리자가 영영 로그인할
            # 수 없다. 앱이 뜰 때마다 현재 설정값과 어긋난 부분만 보정한다 (비밀번호·상태처럼
            # 운영 중 사람이 바꿨을 값은 건드리지 않는다).
            changed = False
            if admin.email != settings.bootstrap_admin_email:
                admin.email = settings.bootstrap_admin_email
                changed = True
            if admin.department_id != department_id:
                admin.department_id = department_id
                changed = True
            if admin.role_code != EmployeeRole.ADMIN.value:
                admin.role_code = EmployeeRole.ADMIN.value
                changed = True
            if {p.permission_code for p in admin.permissions} != set(PermissionCode):
                admin.permissions = [EmployeePermission(permission_code=code) for code in PermissionCode]
                changed = True
            if changed:
                admin.auth_version += 1
                admin.updated_at = now
                await db.commit()
            return

        admin = Employee(
            employee_code=settings.bootstrap_admin_id,
            name=settings.bootstrap_admin_name,
            email=settings.bootstrap_admin_email,
            department_id=department_id,
            password_hash=security.hash_password(settings.bootstrap_admin_password),
            status=EmployeeStatus.ACTIVE,
            role_code=EmployeeRole.ADMIN.value,
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
