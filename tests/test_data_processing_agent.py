import base64
import csv
import io

from agent_runtime.data_processing.agent import run


def _invoke(payload: dict) -> dict:
    return run(payload)


def test_processing_returns_artifacts_and_audit(monkeypatch):
    monkeypatch.setenv("DATA_ANONYMIZATION_KEY", "test-only-secret")
    result = _invoke(
        {
            "raw_requirement": "지역별 결제 데이터를 CSV와 보고서로 제공",
            "analysis": {"delivery_channel": "api", "output_formats": ["csv", "report"]},
            "selected_rows": [
                {"customer_id": "user-1", "region": " 수도권 ", "amount": "1000"},
                {"customer_id": "user-2", "region": "수도권", "amount": ""},
                {"customer_id": "user-2", "region": "수도권", "amount": ""},
            ],
            "column_policies": {"amount": {"data_type": "number"}},
            "approval_audit": {
                "stage_run_id": 11,
                "sha256": "abc123",
                "approved_at": "2026-07-31T00:00:00+00:00",
                "reviewer_id": 3,
                "reviewer_name": "검토자",
            },
        }
    )

    assert result["ok"] is True
    data = result["data"]
    assert data["quality_report"]["duplicates_removed"] == 1
    assert data["quality_report"]["imputation_count"] == 1
    assert data["api_result"]["items"][0]["customer_id"].startswith("anon_")
    assert data["processing_explanation"]["format_conversion"]["csv_encoding"] == "utf-8-sig"
    assert data["execution_audit"]["approved_selection_stage_run_id"] == 11
    assert data["execution_audit"]["approved_selection_sha256"] == "abc123"

    decoded = base64.b64decode(data["csv_artifact"]["content_base64"]).decode("utf-8-sig")
    rows = list(csv.DictReader(io.StringIO(decoded)))
    assert len(rows) == 2


def test_direct_identifier_requires_key(monkeypatch):
    monkeypatch.delenv("DATA_ANONYMIZATION_KEY", raising=False)
    monkeypatch.delenv("ANON_HASH_SALT", raising=False)
    result = _invoke({"selected_rows": [{"email": "person@example.com"}]})
    assert result["ok"] is False
    assert result["failure_code"] == "PROCESSING_RULE_INVALID"


def test_empty_selected_rows_fails():
    result = _invoke({"selection": {"selected_tables": [{"table": "transaction_pseudonymized"}]}})
    assert result["ok"] is False
    assert "selected_rows" in result["error_message"]
