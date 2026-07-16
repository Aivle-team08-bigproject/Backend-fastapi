from fastapi import HTTPException, status
from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from app.models.enums import RequirementStatus, TaskStatus
from app.models.requirement import Requirement, RequirementStatusHistory
from app.models.task import Task
from app.schemas.requirement import RequirementCreate, RequirementUpdate


ALLOWED_TRANSITIONS: dict[RequirementStatus, set[RequirementStatus]] = {
    RequirementStatus.DRAFT: {
        RequirementStatus.REVIEW,
        RequirementStatus.CANCELLED,
    },
    RequirementStatus.REVIEW: {
        RequirementStatus.APPROVED,
        RequirementStatus.REJECTED,
        RequirementStatus.DRAFT,
        RequirementStatus.CANCELLED,
    },
    RequirementStatus.APPROVED: {
        RequirementStatus.IN_PROGRESS,
        RequirementStatus.CANCELLED,
    },
    RequirementStatus.IN_PROGRESS: {
        RequirementStatus.COMPLETED,
        RequirementStatus.CANCELLED,
    },
    RequirementStatus.REJECTED: {
        RequirementStatus.DRAFT,
        RequirementStatus.CANCELLED,
    },
    RequirementStatus.COMPLETED: set(),
    RequirementStatus.CANCELLED: set(),
}


def get_or_404(db: Session, requirement_id: int) -> Requirement:
    requirement = db.get(Requirement, requirement_id)
    if not requirement:
        raise HTTPException(status_code=404, detail="요구사항을 찾을 수 없습니다.")
    return requirement


def to_read_dict(requirement: Requirement) -> dict:
    tasks = requirement.tasks
    task_count = len(tasks)
    completed_task_count = sum(1 for task in tasks if task.status == TaskStatus.DONE)
    progress = round(sum(task.progress for task in tasks) / task_count) if task_count else 0
    return {
        **{column.name: getattr(requirement, column.name) for column in Requirement.__table__.columns},
        "progress": progress,
        "task_count": task_count,
        "completed_task_count": completed_task_count,
    }


def create(db: Session, payload: RequirementCreate) -> Requirement:
    requirement = Requirement(
        title=payload.title,
        description=payload.description,
        business_purpose=payload.business_purpose,
        acceptance_criteria=payload.acceptance_criteria,
        requester=payload.requester,
        owner=payload.owner,
        priority=payload.priority,
        due_date=payload.due_date,
        created_by=payload.actor,
        updated_by=payload.actor,
    )
    db.add(requirement)
    db.flush()
    db.add(
        RequirementStatusHistory(
            requirement_id=requirement.id,
            from_status=None,
            to_status=requirement.status,
            reason="요구사항 생성",
            changed_by=payload.actor,
        )
    )
    db.commit()
    db.refresh(requirement)
    return requirement


def list_requirements(
    db: Session,
    page: int,
    size: int,
    keyword: str | None,
    req_status: RequirementStatus | None,
    owner: str | None,
):
    filters = []
    if keyword:
        filters.append(
            or_(
                Requirement.title.ilike(f"%{keyword}%"),
                Requirement.description.ilike(f"%{keyword}%"),
            )
        )
    if req_status:
        filters.append(Requirement.status == req_status)
    if owner:
        filters.append(Requirement.owner == owner)

    total = db.scalar(select(func.count(Requirement.id)).where(*filters)) or 0
    items = db.scalars(
        select(Requirement)
        .where(*filters)
        .order_by(Requirement.id.desc())
        .offset((page - 1) * size)
        .limit(size)
    ).all()
    return items, total


def update(db: Session, requirement: Requirement, payload: RequirementUpdate) -> Requirement:
    data = payload.model_dump(exclude_unset=True, exclude={"actor"})
    for key, value in data.items():
        setattr(requirement, key, value)
    requirement.updated_by = payload.actor
    db.commit()
    db.refresh(requirement)
    return requirement


def change_status(
    db: Session,
    requirement: Requirement,
    new_status: RequirementStatus,
    reason: str | None,
    actor: str,
) -> Requirement:
    old_status = requirement.status
    if new_status == old_status:
        return requirement
    if new_status not in ALLOWED_TRANSITIONS[old_status]:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"허용되지 않은 상태 변경입니다: {old_status.value} → {new_status.value}",
        )

    if new_status == RequirementStatus.COMPLETED:
        unfinished = db.scalar(
            select(func.count(Task.id)).where(
                Task.requirement_id == requirement.id,
                Task.status.not_in([TaskStatus.DONE, TaskStatus.CANCELLED]),
            )
        )
        if unfinished:
            raise HTTPException(
                status_code=409,
                detail="완료되지 않은 작업이 있어 요구사항을 완료할 수 없습니다.",
            )

    requirement.status = new_status
    requirement.updated_by = actor
    db.add(
        RequirementStatusHistory(
            requirement_id=requirement.id,
            from_status=old_status,
            to_status=new_status,
            reason=reason,
            changed_by=actor,
        )
    )
    db.commit()
    db.refresh(requirement)
    return requirement
