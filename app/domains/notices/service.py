from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.common.errors import bad_request, not_found
from app.common.time_utils import utcnow
from app.domains.employees.model import AdminAuditLog, Employee
from app.domains.notices.model import Notice, NoticeStatus
from app.domains.notices.schema import NoticeUpdateRequest, NoticeWriteRequest


def _published_filter():
    return Notice.status == NoticeStatus.PUBLISHED, Notice.published_at.is_not(None)


async def list_published(db: AsyncSession, page: int, page_size: int) -> tuple[list[tuple[Notice, Employee]], int]:
    published, has_date = _published_filter()
    total = int((await db.scalar(select(func.count(Notice.id)).where(published, has_date))) or 0)
    rows = (
        await db.execute(
            select(Notice, Employee)
            .join(Employee, Employee.id == Notice.created_by_employee_id)
            .where(published, has_date)
            .order_by(Notice.published_at.desc(), Notice.id.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
    ).all()
    return list(rows), total


async def get_latest(db: AsyncSession) -> tuple[Notice, Employee] | None:
    published, has_date = _published_filter()
    return (
        await db.execute(
            select(Notice, Employee)
            .join(Employee, Employee.id == Notice.created_by_employee_id)
            .where(published, has_date)
            .order_by(Notice.published_at.desc(), Notice.id.desc())
            .limit(1)
        )
    ).first()


async def get_published(db: AsyncSession, notice_id: int) -> tuple[Notice, Employee]:
    published, has_date = _published_filter()
    row = (
        await db.execute(
            select(Notice, Employee)
            .join(Employee, Employee.id == Notice.created_by_employee_id)
            .where(Notice.id == notice_id, published, has_date)
        )
    ).first()
    if row is None:
        raise not_found("NOTICE_NOT_FOUND", "공지사항을 찾을 수 없습니다.")
    return row


def _validate_status_transition(current: NoticeStatus, next_status: NoticeStatus) -> None:
    if current == next_status:
        return
    allowed = {
        NoticeStatus.DRAFT: {NoticeStatus.PUBLISHED, NoticeStatus.ARCHIVED},
        NoticeStatus.PUBLISHED: {NoticeStatus.ARCHIVED},
        NoticeStatus.ARCHIVED: set(),
    }
    if next_status not in allowed[current]:
        raise bad_request("NOTICE_STATUS_INVALID", "허용되지 않는 공지사항 상태 변경입니다.")


async def create_notice(db: AsyncSession, payload: NoticeWriteRequest, actor: Employee) -> Notice:
    now = utcnow()
    published_at = now if payload.status == NoticeStatus.PUBLISHED else None
    notice = Notice(
        title=payload.title,
        content=payload.content,
        status=payload.status,
        created_by_employee_id=actor.id,
        updated_by_employee_id=actor.id,
        published_at=published_at,
        created_at=now,
        updated_at=now,
    )
    db.add(notice)
    await db.flush()
    db.add(AdminAuditLog(
        actor_employee_code=actor.employee_code,
        action="NOTICE_CREATED",
        detail=f"notice_id={notice.id};status={notice.status.value}",
        created_at=now,
    ))
    await db.commit()
    await db.refresh(notice)
    return notice


async def update_notice(
    db: AsyncSession, notice_id: int, payload: NoticeUpdateRequest, actor: Employee
) -> Notice:
    notice = await db.get(Notice, notice_id)
    if notice is None:
        raise not_found("NOTICE_NOT_FOUND", "공지사항을 찾을 수 없습니다.")
    next_status = payload.status or notice.status
    _validate_status_transition(notice.status, next_status)
    if payload.title is not None:
        notice.title = payload.title
    if payload.content is not None:
        notice.content = payload.content
    if notice.status != next_status:
        notice.status = next_status
        if next_status == NoticeStatus.PUBLISHED and notice.published_at is None:
            notice.published_at = utcnow()
    notice.updated_by_employee_id = actor.id
    notice.updated_at = utcnow()
    db.add(AdminAuditLog(
        actor_employee_code=actor.employee_code,
        action="NOTICE_UPDATED",
        detail=f"notice_id={notice.id};status={notice.status.value}",
        created_at=notice.updated_at,
    ))
    await db.commit()
    await db.refresh(notice)
    return notice
