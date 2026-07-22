from app.pipeline.celery_app import celery_app
from app.pipeline.contracts import AgentEvent, AgentEventType, AgentExecutionRequest
from app.pipeline.tasks import health_check


def test_celery_uses_configured_broker(settings):
    assert celery_app.conf.broker_url == settings.celery_broker_url
    assert celery_app.conf.result_backend == settings.celery_result_backend


def test_agent_event_contract_accepts_progress_event():
    event = AgentEvent(
        event_type=AgentEventType.PROGRESS,
        message="요구사항 분석 중",
        progress_percent=25,
        payload={"agent_name": "requirement-analysis-agent"},
    )
    request = AgentExecutionRequest(
        run_id="run-1",
        stage_run_id="stage-1",
        stage_code="REQUIREMENT_ANALYSIS",
    )

    assert event.progress_percent == 25
    assert request.stage_code == "REQUIREMENT_ANALYSIS"


def test_health_check_task_returns_executor():
    assert health_check.run() == {"status": "ok", "executor": "celery"}
