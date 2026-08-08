from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.security_deps import CurrentAuth, get_current_auth, require_role
from app.db.session import get_db
from app.domains.employees.model import Employee, EmployeeRole
from app.domains.notices import service
from app.domains.notices.model import Notice
from app.domains.notices.schema import (
    NoticeDetail,
    NoticeLatestResponse,
    NoticeListItem,
    NoticeListResponse,
    NoticeUpdateRequest,
    NoticeWriteRequest,
)

router = APIRouter(prefix="/api/v1", tags=["notices"])


def _item(row: tuple[Notice, Employee]) -> NoticeListItem:
    notice, author = row
    return NoticeListItem(
        id=notice.id,
        title=notice.title,
        author_name=author.name,
        published_at=notice.published_at,
    )


@router.get("/notices", response_model=NoticeListResponse)
async def list_notices(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    auth: CurrentAuth = Depends(get_current_auth),
    db: AsyncSession = Depends(get_db),
) -> NoticeListResponse:
    rows, total = await service.list_published(db, page, page_size)
    return NoticeListResponse(items=[_item(row) for row in rows], total_count=total, page=page, page_size=page_size)


@router.get("/notices/latest", response_model=NoticeLatestResponse)
async def latest_notice(
    auth: CurrentAuth = Depends(get_current_auth), db: AsyncSession = Depends(get_db)
) -> NoticeLatestResponse:
    row = await service.get_latest(db)
    return NoticeLatestResponse(item=_item(row) if row else None)


@router.get("/notices/{notice_id}", response_model=NoticeDetail)
async def notice_detail(
    notice_id: int, auth: CurrentAuth = Depends(get_current_auth), db: AsyncSession = Depends(get_db)
) -> NoticeDetail:
    notice, author = await service.get_published(db, notice_id)
    return NoticeDetail(**_item((notice, author)).model_dump(), content=notice.content)


@router.post("/admin/notices", response_model=NoticeDetail, status_code=status.HTTP_201_CREATED)
async def create_notice(
    payload: NoticeWriteRequest,
    auth: CurrentAuth = Depends(require_role(EmployeeRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> NoticeDetail:
    notice = await service.create_notice(db, payload, auth.employee)
    return NoticeDetail(
        id=notice.id,
        title=notice.title,
        content=notice.content,
        author_name=auth.employee.name,
        published_at=notice.published_at or notice.created_at,
    )


@router.patch("/admin/notices/{notice_id}", response_model=NoticeDetail)
async def update_notice(
    notice_id: int,
    payload: NoticeUpdateRequest,
    auth: CurrentAuth = Depends(require_role(EmployeeRole.ADMIN)),
    db: AsyncSession = Depends(get_db),
) -> NoticeDetail:
    notice = await service.update_notice(db, notice_id, payload, auth.employee)
    return NoticeDetail(
        id=notice.id,
        title=notice.title,
        content=notice.content,
        author_name=auth.employee.name,
        published_at=notice.published_at or notice.created_at,
    )
