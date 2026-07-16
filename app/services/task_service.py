from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.enums import TaskStatus
from app.models.requirement import Requirement
from app.models.task import Task, TaskStatusHistory
from app.schemas.task import TaskCreate, TaskUpdate


ALLOWED_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.TODO: {TaskStatus.IN_PROGRESS, TaskStatus.BLOCKED, TaskStatus.CANCELLED},
    TaskStatus.IN_PROGRESS: {TaskStatus.DONE, TaskStatus.BLOCKED, TaskStatus.CANCELLED},
    TaskStatus.BLOCKED: {TaskStatus.TODO, TaskStatus.IN_PROGRESS, TaskStatus.CANCELLED},
    TaskStatus.DONE: {TaskStatus.IN_PROGRESS},
    TaskStatus.CANCELLED: set(),
}


def get_or_404(db: Session, task_id: int) -> Task:
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="작업을 찾을 수 없습니다.")
    return task


def create(db: Session, requirement: Requirement, payload: TaskCreate) -> Task:
    task = Task(
        requirement_id=requirement.id,
        title=payload.title,
        description=payload.description,
        assignee=payload.assignee,
        priority=payload.priority,
        start_date=payload.start_date,
        due_date=payload.due_date,
        created_by=payload.actor,
        updated_by=payload.actor,
    )
    db.add(task)
    db.flush()
    db.add(
        TaskStatusHistory(
            task_id=task.id,
            from_status=None,
            to_status=task.status,
            reason="작업 생성",
            changed_by=payload.actor,
        )
    )
    db.commit()
    db.refresh(task)
    return task


def update(db: Session, task: Task, payload: TaskUpdate) -> Task:
    data = payload.model_dump(exclude_unset=True, exclude={"actor"})
    for key, value in data.items():
        setattr(task, key, value)

    if task.start_date and task.due_date and task.due_date < task.start_date:
        raise HTTPException(status_code=422, detail="due_date는 start_date보다 빠를 수 없습니다.")

    if task.status == TaskStatus.DONE:
        task.progress = 100
    task.updated_by = payload.actor
    db.commit()
    db.refresh(task)
    return task


def change_status(
    db: Session,
    task: Task,
    new_status: TaskStatus,
    reason: str | None,
    actor: str,
) -> Task:
    old_status = task.status
    if new_status == old_status:
        return task
    if new_status not in ALLOWED_TRANSITIONS[old_status]:
        raise HTTPException(
            status_code=409,
            detail=f"허용되지 않은 상태 변경입니다: {old_status.value} → {new_status.value}",
        )

    task.status = new_status
    task.progress = 100 if new_status == TaskStatus.DONE else min(task.progress, 99)
    task.updated_by = actor
    db.add(
        TaskStatusHistory(
            task_id=task.id,
            from_status=old_status,
            to_status=new_status,
            reason=reason,
            changed_by=actor,
        )
    )
    db.commit()
    db.refresh(task)
    return task
