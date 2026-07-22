from uuid import uuid4

from fastapi.testclient import TestClient


def test_create_request_then_read_pipeline_run(client: TestClient):
    marker = uuid4().hex[:8]
    created = client.post(
        "/api/v1/data-requests",
        json={
            "raw_requirement": f"서울 지역 시간대별 결제 밀도를 분석해주세요. {marker}",
            "title": f"프론트 연동 테스트 {marker}",
            "requester_name": f"연동 테스트 요청자 {marker}",
        },
    )

    assert created.status_code == 202
    created_body = created.json()
    assert created_body["request_status"] == "QUEUED"
    assert created_body["run_status"] == "QUEUED"
    assert created_body["current_stage"] == "REQUIREMENT_ANALYSIS"

    response = client.get(f"/api/v1/runs/{created_body['run_id']}")

    assert response.status_code == 200
    body = response.json()
    assert body["request_no"] == created_body["request_no"]
    assert body["request_title"] == f"프론트 연동 테스트 {marker}"
    assert body["stages"][0]["status"] == "PENDING"
    assert body["events"][0]["event_type"] == "progress"

    response = client.get("/api/v1/runs/2147483647")

    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "PIPELINE_RUN_NOT_FOUND"
