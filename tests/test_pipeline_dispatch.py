import asyncio
from types import SimpleNamespace
from unittest.mock import Mock

from app.domains.pipeline.model import DataRequest, PipelineRun, StageRun
from app.domains.pipeline.schema import CreateDataRequestRequest
from app.domains.pipeline import service


class FakeAsyncSession:
    def __init__(self):
        self.added = []
        self.next_id = 1
        self.commits = 0

    async def scalar(self, statement):
        return None

    def add(self, value):
        self.added.append(value)

    async def flush(self):
        for value in self.added:
            if hasattr(value, "id") and value.id is None:
                value.id = self.next_id
                self.next_id += 1

    async def commit(self):
        self.commits += 1


def test_create_data_request_persists_id_before_celery_publish(monkeypatch):
    db = FakeAsyncSession()
    apply_async = Mock()
    monkeypatch.setattr(service.process_pipeline_run, "apply_async", apply_async)

    response = asyncio.run(
        service.create_data_request(
            db,
            CreateDataRequestRequest(
                raw_requirement="서울 지역 결제 데이터를 CSV로 제공해주세요.",
                title="서울 결제 데이터",
                requester_name="테스트 요청자",
            ),
            SimpleNamespace(id=42, name="테스트 담당자"),
        )
    )

    run = next(value for value in db.added if isinstance(value, PipelineRun))
    data_request = next(value for value in db.added if isinstance(value, DataRequest))
    stages = [value for value in db.added if isinstance(value, StageRun)]

    assert response.run_id == run.id
    assert response.celery_task_id == run.celery_task_id
    assert response.run_status.value == "QUEUED"
    assert data_request.status.value == "QUEUED"
    assert data_request.owner_id == 42
    assert data_request.owner_name == "테스트 담당자"
    assert [stage.status.value for stage in stages] == ["PENDING"] * 3
    apply_async.assert_called_once_with(args=[run.id], task_id=run.celery_task_id)
    assert db.commits == 1
