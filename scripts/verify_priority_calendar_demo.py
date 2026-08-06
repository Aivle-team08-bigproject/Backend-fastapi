"""seed_priority_calendar_demo.py로 넣은 더미 작업이 대시보드/캘린더에 제대로 잡히는지 확인한다.

비밀번호·토큰 없이 get_current_auth만 오버라이드해서 실제 라우트를 그대로 태운다.
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
            return CurrentAuth(employee=employee, session=MagicMock(), must_change_password=False, permissions=permissions)

        app.dependency_overrides[get_current_auth] = _override
        try:
            transport = ASGITransport(app=app)
            async with AsyncClient(transport=transport, base_url="http://verify.local") as client:
                dashboard = (await client.get("/api/v1/dashboard")).json()
                print("=== priority_cards ===")
                for card in dashboard["priority_cards"]:
                    print(f"  {card['priority_code']:12s} {card['label']:10s} count={card['count']}")
                print(f"active_task_count={dashboard['active_task_count']}")
                print(f"calendar_events={len(dashboard['calendar_events'])}")
                for event in dashboard["calendar_events"]:
                    print(f"  {event['event_date']}  {event['event_type']:14s} {event['request_no']} {event['title']}")

                tasks = (await client.get("/api/v1/dashboard/tasks?scope=mine&page_size=30")).json()
                print(f"=== dashboard/tasks total_count={tasks['total_count']} ===")
                for item in tasks["items"]:
                    print(f"  {item['request_no']} status_group={item['status_group_code']:14s} priority={str(item['priority_code']):11s} stage={item['stage_group_code']}")
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
