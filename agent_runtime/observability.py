"""Best-effort observability tools for agent completion events."""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from uuid import uuid4

from strands import tool


_LOG_WRITE_LOCK = Lock()
_AGENT_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_DEFAULT_LOG_DIR = Path(__file__).resolve().parents[1] / "agent_logs"


def _log_dir() -> Path:
    configured_dir = os.getenv("AGENT_LOG_DIR")
    return Path(configured_dir) if configured_dir else _DEFAULT_LOG_DIR


def _write_completion_log(
    agent_name: str,
    completed_tasks: list[str],
    summary: str,
) -> dict:
    if not _AGENT_NAME_RE.fullmatch(agent_name):
        return {"ok": False, "error": "invalid agent name"}
    if not completed_tasks or any(not isinstance(task, str) or not task.strip() for task in completed_tasks):
        return {"ok": False, "error": "completed_tasks must contain non-empty strings"}
    if not isinstance(summary, str) or not summary.strip():
        return {"ok": False, "error": "summary must be a non-empty string"}

    record = {
        "event_id": str(uuid4()),
        "agent_name": agent_name,
        "completed_tasks": completed_tasks,
        "summary": summary,
        "completed_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        log_dir = _log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        log_path = log_dir / f"{agent_name}.jsonl"
        with _LOG_WRITE_LOCK, log_path.open("a", encoding="utf-8") as file:
            file.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:  # noqa: BLE001 - observability must never fail the pipeline
        return {"ok": False, "error": f"completion log unavailable: {exc}"}
    return {
        "ok": True,
        "agent_name": agent_name,
        "event_id": record["event_id"],
        "next_action": "로깅 도구 호출은 최종 응답이 아니다. 이제 요청된 전체 JSON을 최종 응답으로 반환하라.",
    }


def build_agent_completion_tool(agent_name: str):
    """Create a completion tool whose agent identity is fixed by the caller."""

    @tool(name="log_agent_completion")
    def log_agent_completion(completed_tasks: list[str], summary: str) -> dict:
        """Record the work completed by this agent after the requested task is done.

        This is best-effort observability. A failed write is returned to the model as a
        tool result and must not be treated as a task failure.
        """

        return _write_completion_log(agent_name, completed_tasks, summary)

    return log_agent_completion
