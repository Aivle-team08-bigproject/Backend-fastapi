"""Strands tool entry point for deterministic data processing."""

from strands import tool

from agent_runtime.data_processing.processor import ProcessingError, process_payload


@tool
def run(payload: dict) -> dict:
    """Process selected rows and return an auditable artifact envelope."""
    try:
        return {"ok": True, "data": process_payload(payload), "error_message": None}
    except (ProcessingError, ValueError) as exc:
        return {
            "ok": False,
            "data": None,
            "error_message": str(exc),
            "failure_code": "PROCESSING_RULE_INVALID",
        }
