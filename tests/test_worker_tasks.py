import asyncio

import pytest

from app.domains.pipeline import executor as tasks


class FakeLogSession:
    def __init__(self):
        self.rolled_back = False
        self.closed = False

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        self.closed = True

    async def rollback(self):
        self.rolled_back = True


def test_agent_log_failure_rolls_back_only_isolated_session(monkeypatch):
    log_db = FakeLogSession()

    async def fail_record_agent_log(db, **kwargs):
        assert db is log_db
        raise RuntimeError("log commit failed")

    monkeypatch.setattr(tasks, "AsyncSessionLocal", lambda: log_db)
    monkeypatch.setattr(tasks, "record_agent_log", fail_record_agent_log)

    with pytest.raises(RuntimeError, match="log commit failed"):
        asyncio.run(
            tasks._record_agent_log_isolated(
                run_id=7,
                execution_id="task-7",
                message="test log",
            )
        )

    assert log_db.rolled_back is True
    assert log_db.closed is True


def test_persistable_stage_output_excludes_csv_base64():
    original = {
        "processed_columns": ["region"],
        "csv_artifact": {"content_base64": "c2VjcmV0"},
    }

    persisted = tasks._persistable_stage_output(original)

    assert "csv_artifact" not in persisted
    assert original["csv_artifact"]["content_base64"] == "c2VjcmV0"
