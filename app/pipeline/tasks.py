"""Celery worker 등록 task.

`execute_stage`는 pipeline_runs/stage_runs 모델과 event repository가 추가된 뒤
`AgentRunner`를 호출하도록 확장한다. 현재 health task는 Redis-broker와 worker가
정상 연결됐는지 확인하는 로컬 smoke test 용도다.
"""

from app.pipeline.celery_app import celery_app


@celery_app.task(name="pipeline.health_check")
def health_check() -> dict[str, str]:
    return {"status": "ok", "executor": "celery"}
