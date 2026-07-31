import csv

from agent_runtime.data_processing.agent import run as run_processing
from agent_runtime.data_retrieval.agent import run as run_retrieval


def test_retrieval_hands_validated_csv_to_processing(tmp_path, monkeypatch):
    source = tmp_path / "selected.csv"
    with source.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(csv_file, fieldnames=["customer_id", "amount"])
        writer.writeheader()
        writer.writerow({"customer_id": "user-1", "amount": "1000"})
        writer.writerow({"customer_id": "user-2", "amount": "2000"})

    monkeypatch.setenv("DATA_RETRIEVAL_ALLOWED_ROOT", str(tmp_path))
    monkeypatch.setenv("DATA_ANONYMIZATION_KEY", "test-only-secret")

    retrieval_result = run_retrieval(
        {
            "source_csv_path": str(source),
            "selection": {
                "selected_tables": [{"table": "transaction_pseudonymized"}],
                "selection_query": {"filters": {}},
            },
        }
    )
    assert retrieval_result["ok"] is True
    assert retrieval_result["data"]["row_count"] == 2
    assert retrieval_result["data"]["raw_rows_stored_in_database"] is False

    processing_result = run_processing(
        {
            "analysis": {"delivery_channel": "api", "output_formats": ["csv"]},
            "retrieval": retrieval_result["data"],
        }
    )
    assert processing_result["ok"] is True
    assert processing_result["data"]["quality_report"]["input_row_count"] == 2
    assert processing_result["data"]["api_result"]["items"][0]["customer_id"].startswith("anon_")


def test_retrieval_rejects_csv_outside_allowed_root(tmp_path, monkeypatch):
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    source = tmp_path / "outside.csv"
    source.write_text("id\n1\n", encoding="utf-8")
    monkeypatch.setenv("DATA_RETRIEVAL_ALLOWED_ROOT", str(allowed))

    result = run_retrieval({"source_csv_path": str(source)})

    assert result["ok"] is False
    assert "must be inside" in result["error_message"]


def test_processing_rejects_changed_retrieved_csv(tmp_path, monkeypatch):
    source = tmp_path / "selected.csv"
    source.write_text("id\n1\n", encoding="utf-8")
    monkeypatch.setenv("DATA_RETRIEVAL_ALLOWED_ROOT", str(tmp_path))

    retrieval = run_retrieval({"source_csv_path": str(source)})["data"]
    selected = retrieval["source_csv_path"]
    with open(selected, "w", encoding="utf-8") as csv_file:
        csv_file.write("id\n2\n")

    result = run_processing(
        {
            "analysis": {"delivery_channel": "api", "output_formats": ["csv"]},
            "retrieval": retrieval,
        }
    )

    assert result["ok"] is False
    assert "checksum mismatch" in result["error_message"]


def test_retrieval_applies_identified_filters_before_processing(tmp_path, monkeypatch):
    source = tmp_path / "transactions.csv"
    with source.open("w", encoding="utf-8", newline="") as csv_file:
        writer = csv.DictWriter(
            csv_file,
            fieldnames=["customer_id", "gender", "age_band", "destination_country_name", "spend_category"],
        )
        writer.writeheader()
        writer.writerows(
            [
                {
                    "customer_id": "1",
                    "gender": "F",
                    "age_band": "20대",
                    "destination_country_name": "일본",
                    "spend_category": "lodging",
                },
                {
                    "customer_id": "2",
                    "gender": "M",
                    "age_band": "20대",
                    "destination_country_name": "일본",
                    "spend_category": "lodging",
                },
                {
                    "customer_id": "3",
                    "gender": "F",
                    "age_band": "30대",
                    "destination_country_name": "일본",
                    "spend_category": "dining",
                },
                {
                    "customer_id": "4",
                    "gender": "F",
                    "age_band": "20대",
                    "destination_country_name": "태국",
                    "spend_category": "lodging",
                },
            ]
        )
    monkeypatch.setenv("DATA_RETRIEVAL_ALLOWED_ROOT", str(tmp_path))

    result = run_retrieval(
        {
            "source_csv_path": str(source),
            "selection": {
                "selection_query": {
                    "filters": {
                        "성별": "여성",
                        "연령대": "20대",
                        "국가": "일본",
                        "업종": "숙박",
                    }
                }
            },
        }
    )

    assert result["ok"] is True
    assert result["data"]["input_row_count"] == 4
    assert result["data"]["row_count"] == 1
    assert len(result["data"]["applied_filters"]) == 4


def test_retrieval_blocks_unmapped_filter(tmp_path, monkeypatch):
    source = tmp_path / "transactions.csv"
    source.write_text("id\n1\n", encoding="utf-8")
    monkeypatch.setenv("DATA_RETRIEVAL_ALLOWED_ROOT", str(tmp_path))

    result = run_retrieval(
        {
            "source_csv_path": str(source),
            "selection": {"selection_query": {"filters": {"알수없는조건": "값"}}},
        }
    )

    assert result["ok"] is False
    assert "unmapped selection filter" in result["error_message"]


def test_region_filter_can_match_travel_destination(tmp_path, monkeypatch):
    source = tmp_path / "transactions.csv"
    source.write_text(
        "resident_region,destination_country_name\n서울특별시 강남구,일본\n서울특별시 강남구,태국\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("DATA_RETRIEVAL_ALLOWED_ROOT", str(tmp_path))

    result = run_retrieval(
        {
            "source_csv_path": str(source),
            "selection": {"selection_query": {"filters": {"지역": "일본"}}},
        }
    )

    assert result["ok"] is True
    assert result["data"]["row_count"] == 1
