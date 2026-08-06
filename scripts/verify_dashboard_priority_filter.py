"""GET /api/v1/dashboard/tasks의 priority/status 모순 조합 검증을 인증 없이 확인한다.

get_current_auth 의존성만 오버라이드해서 실제 라우트 코드를 그대로 태운다.
비밀번호·토큰은 전혀 쓰지 않는다 — DB에서 직접 조회한 직원 레코드로 CurrentAuth를 구성한다.
"""

import asyncio
from unittest.mock import MagicMock

from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.common.security_deps import CurrentAuth, get_current_auth
from app.db.session import AsyncSessionLocal, engine
from app.domains.employees.model import Employee, EmployeePermission
from app.main import app

TEST_EMAIL = "whddhs6645@company.com"


async def _run() -> None:
    async with AsyncSessionLocal() as db:
        employee = await db.scalar(select(Employee).where(Employee.email == TEST_EMAIL))
        if employee is None:
            raise RuntimeError(f"검증용 계정을 찾을 수 없습니다: {TEST_EMAIL}")
        permission_rows = await db.scalars(
            select(EmployeePermission).where(EmployeePermission.employee_id == employee.id)
        )
        permissions = {row.permission_code for row in permission_rows}

        async def _override() -> CurrentAuth:
            return CurrentAuth(
                employee=employee,
                session=MagicMock(),
                must_change_password=False,
                permissions=permissions,
            )

        app.dependency_overrides[get_current_auth] = _override
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://verify.local") as client:
                cases = [
                    ("priority=FINAL&status=completed", "모순 조합 - 422 기대"),
                    ("priority=FINAL&status=waiting_review", "정상 조합 - 200 기대"),
                    ("priority=FINAL", "priority만 - 200 기대"),
                    ("status=completed", "status만 - 200 기대"),
                ]
                for query, note in cases:
                    response = await client.get(f"/api/v1/dashboard/tasks?{query}")
                    print(f"{note:30s} GET /api/v1/dashboard/tasks?{query} -> {response.status_code}")
                    if response.status_code == 422:
                        print(f"  detail: {response.json().get('detail')}")
        finally:
            app.dependency_overrides.pop(get_current_auth, None)


def main() -> int:
    try:
        asyncio.run(_run())
    finally:
        asyncio.run(engine.dispose())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
