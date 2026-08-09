from app.domains.pipeline.service import result_download_filename


def test_result_download_filename_includes_request_and_run_identifiers():
    assert result_download_filename("REQ-20260808-1B1659", 267) == "REQ-20260808-1B1659-run-267-result.csv"
