# DB 설정과 테이블 정리는 conftest.py(REQUIREMENTS_DATABASE_URL + _clean_test_database)가 담당한다.
from fastapi.testclient import TestClient
from app.main import app


def test_full_requirement_and_task_flow():
    with TestClient(app) as client:
        response = client.post(
            "/api/v1/requirements",
            json={
                "title": "관리자 요구사항 관리",
                "description": "요구사항 CRUD 및 상태·작업 관리 기능",
                "priority": "HIGH",
                "actor": "admin",
            },
        )
        assert response.status_code == 201
        requirement_id = response.json()["id"]
        assert response.json()["status"] == "DRAFT"

        response = client.patch(
            f"/api/v1/requirements/{requirement_id}/status",
            json={"status": "REVIEW", "reason": "검토 요청", "actor": "admin"},
        )
        assert response.status_code == 200
        assert response.json()["status"] == "REVIEW"

        task = client.post(
            f"/api/v1/requirements/{requirement_id}/tasks",
            json={
                "title": "요구사항 목록 API 구현",
                "assignee": "developer01",
                "priority": "HIGH",
                "actor": "admin",
            },
        )
        assert task.status_code == 201
        task_id = task.json()["id"]

        task = client.patch(
            f"/api/v1/tasks/{task_id}/status",
            json={"status": "IN_PROGRESS", "actor": "developer01"},
        )
        assert task.status_code == 200

        task = client.patch(
            f"/api/v1/tasks/{task_id}",
            json={"progress": 70, "actor": "developer01"},
        )
        assert task.status_code == 200
        assert task.json()["progress"] == 70

        task = client.patch(
            f"/api/v1/tasks/{task_id}/status",
            json={"status": "DONE", "reason": "개발 완료", "actor": "developer01"},
        )
        assert task.status_code == 200
        assert task.json()["progress"] == 100

        detail = client.get(f"/api/v1/requirements/{requirement_id}")
        assert detail.status_code == 200
        assert detail.json()["progress"] == 100
        assert detail.json()["completed_task_count"] == 1

        history = client.get(f"/api/v1/tasks/{task_id}/status-history")
        assert history.status_code == 200
        assert len(history.json()) == 3
