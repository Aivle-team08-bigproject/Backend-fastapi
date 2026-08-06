"""이미 가입 신청된 계정을 개발용 슈퍼 관리자로 승격한다.

비밀번호는 건드리지 않는다 — 본인이 가입 시 설정한 비밀번호 그대로 로그인한다.
이 모듈은 FastAPI startup, API 라우터, worker에서 import하지 않는다.
운영자가 필요할 때 명시적으로 한 번 실행한다.
"""

import argparse
import asyncio
import sys

from sqlalchemy import delete, select

from app.common.time_utils import utcnow
from app.db.session import AsyncSessionLocal, engine
from app.domains.employees.model import (
    Employee,
    EmployeePermission,
    EmployeeRole,
    EmployeeStatus,
    PermissionCode,
)


async def _promote(email: str) -> Employee:
    async with AsyncSessionLocal() as db:
        employee = await db.scalar(select(Employee).where(Employee.email == email.strip().lower()))
        if employee is None:
            raise RuntimeError(f"해당 이메일의 가입 신청 계정을 찾을 수 없습니다: {email}")

        employee.status = EmployeeStatus.ACTIVE
        employee.role_code = EmployeeRole.ADMIN.value
        employee.failed_login_count = 0
        employee.updated_at = utcnow()

        await db.execute(delete(EmployeePermission).where(EmployeePermission.employee_id == employee.id))
        db.add_all(EmployeePermission(employee_id=employee.id, permission_code=code) for code in PermissionCode)

        await db.commit()
        await db.refresh(employee)
        return employee


def main() -> int:
    parser = argparse.ArgumentParser(description="가입된 계정을 개발용 슈퍼 관리자(ADMIN, 전체 권한)로 승격합니다.")
    parser.add_argument("--email", required=True, help="승격할 계정의 로그인 이메일")
    args = parser.parse_args()

    try:
        employee = asyncio.run(_promote(args.email))
    except RuntimeError as exc:
        print(f"승격 실패: {exc}", file=sys.stderr)
        return 1
    finally:
        asyncio.run(engine.dispose())

    print(f"승격 완료: {employee.name} <{employee.email}> (employee_code={employee.employee_code})")
    print(f"role={employee.role_code}, status={employee.status.value}, permissions=ALL({len(list(PermissionCode))})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
