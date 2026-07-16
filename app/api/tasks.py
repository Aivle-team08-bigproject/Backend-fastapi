from fastapi import APIRouter, Depends, Response, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models.task import TaskStatusHistory
from app.schemas.task import TaskRead, TaskStatusChange, TaskStatusHistoryRead, TaskUpdate
from app.services import task_service

router = APIRouter(prefix="/tasks", tags=["Tasks"])


@router.get("/{task_id}", response_model=TaskRead)
def get_task(task_id: int, db: Session = Depends(get_db)):
    return task_service.get_or_404(db, task_id)


@router.patch("/{task_id}", response_model=TaskRead)
def update_task(
    task_id: int,
    payload: TaskUpdate,
    db: Session = Depends(get_db),
):
    task = task_service.get_or_404(db, task_id)
    return task_service.update(db, task, payload)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_task(task_id: int, db: Session = Depends(get_db)):
    task = task_service.get_or_404(db, task_id)
    db.delete(task)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.patch("/{task_id}/status", response_model=TaskRead)
def change_task_status(
    task_id: int,
    payload: TaskStatusChange,
    db: Session = Depends(get_db),
):
    task = task_service.get_or_404(db, task_id)
    return task_service.change_status(
        db, task, payload.status, payload.reason, payload.actor
    )


@router.get("/{task_id}/status-history", response_model=list[TaskStatusHistoryRead])
def get_task_status_history(task_id: int, db: Session = Depends(get_db)):
    task_service.get_or_404(db, task_id)
    return db.scalars(
        select(TaskStatusHistory)
        .where(TaskStatusHistory.task_id == task_id)
        .order_by(TaskStatusHistory.changed_at)
    ).all()
