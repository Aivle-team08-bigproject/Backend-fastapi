from fastapi import APIRouter, Depends, Query, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.enums import RequirementStatus
# 요구사항 가져오기
from app.models.requirement import RequirementStatusHistory
from app.models.task import Task
# 사용자가 보내는 JSON과 응답 JSON을 정의한 클래스
from app.schemas.requirement import (
    RequirementCreate,
    RequirementListResponse,
    RequirementRead,
    RequirementStatusChange,
    RequirementStatusHistoryRead,
    RequirementUpdate,
)
from app.schemas.task import TaskCreate, TaskRead
# 실제 비즈니스 로직을 수행하는 부분
from app.services import requirement_service, task_service

# Router 생성
router = APIRouter(prefix="/requirements", tags=["Requirements"])

# 요구사항 생성
@router.post("", response_model=RequirementRead, status_code=status.HTTP_201_CREATED)
def create_requirement(payload: RequirementCreate, db: Session = Depends(get_db)):
    requirement = requirement_service.create(db, payload)
    return requirement_service.to_read_dict(requirement)

# 요구사항 목록 조회
@router.get("", response_model=RequirementListResponse)
def list_requirements(
    page: int = Query(1, ge=1),
    size: int = Query(20, ge=1, le=100),
    keyword: str | None = None,
    req_status: RequirementStatus | None = Query(None, alias="status"),
    owner: str | None = None,
    db: Session = Depends(get_db),
):
    items, total = requirement_service.list_requirements(
        db, page, size, keyword, req_status, owner
    )
    return {
        "items": [requirement_service.to_read_dict(item) for item in items],
        "page": page,
        "size": size,
        "total": total,
    }


@router.get("/{requirement_id}", response_model=RequirementRead)
def get_requirement(requirement_id: int, db: Session = Depends(get_db)):
    requirement = requirement_service.get_or_404(db, requirement_id)
    return requirement_service.to_read_dict(requirement)


@router.patch("/{requirement_id}", response_model=RequirementRead)
def update_requirement(
    requirement_id: int,
    payload: RequirementUpdate,
    db: Session = Depends(get_db),
):
    requirement = requirement_service.get_or_404(db, requirement_id)
    requirement = requirement_service.update(db, requirement, payload)
    return requirement_service.to_read_dict(requirement)


@router.delete("/{requirement_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_requirement(requirement_id: int, db: Session = Depends(get_db)):
    requirement = requirement_service.get_or_404(db, requirement_id)
    db.delete(requirement)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{requirement_id}/status", response_model=RequirementRead)
def change_requirement_status(
    requirement_id: int,
    payload: RequirementStatusChange,
    db: Session = Depends(get_db),
):
    requirement = requirement_service.get_or_404(db, requirement_id)
    requirement = requirement_service.change_status(
        db, requirement, payload.status, payload.reason, payload.actor
    )
    return requirement_service.to_read_dict(requirement)


@router.get(
    "/{requirement_id}/status-history",
    response_model=list[RequirementStatusHistoryRead],
)
def get_requirement_status_history(
    requirement_id: int,
    db: Session = Depends(get_db),
):
    requirement_service.get_or_404(db, requirement_id)
    return db.scalars(
        select(RequirementStatusHistory)
        .where(RequirementStatusHistory.requirement_id == requirement_id)
        .order_by(RequirementStatusHistory.changed_at)
    ).all()


@router.post(
    "/{requirement_id}/tasks",
    response_model=TaskRead,
    status_code=status.HTTP_201_CREATED,
)
def create_task(
    requirement_id: int,
    payload: TaskCreate,
    db: Session = Depends(get_db),
):
    requirement = requirement_service.get_or_404(db, requirement_id)
    return task_service.create(db, requirement, payload)


@router.get("/{requirement_id}/tasks", response_model=list[TaskRead])
def list_tasks(requirement_id: int, db: Session = Depends(get_db)):
    requirement_service.get_or_404(db, requirement_id)
    return db.scalars(
        select(Task)
        .where(Task.requirement_id == requirement_id)
        .order_by(Task.id.desc())
    ).all()
