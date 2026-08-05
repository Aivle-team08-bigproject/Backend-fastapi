"""documents 도메인 — 문서 업로드 → 텍스트 추출 엔드포인트 테스트.

확장자별 파싱 자체는 extractors.py의 순수 함수라 여기선 API 계약(인증/검증/응답 형태)을
중심으로 확인한다.
"""

import io

import pytest
from fastapi.testclient import TestClient

from tests.test_auth_flow import _login_as_admin

EXTRACT_URL = "/api/documents/extract-text"


def _upload(client: TestClient, headers: dict, filename: str, content: bytes, content_type: str):
    return client.post(
        EXTRACT_URL,
        headers=headers,
        files={"file": (filename, io.BytesIO(content), content_type)},
    )


def test_extract_text_rejects_missing_bearer_token(client: TestClient):
    response = _upload(client, {}, "req.txt", b"hello", "text/plain")

    assert response.status_code == 401
    assert response.json()["detail"]["code"] == "UNAUTHORIZED"


def test_extract_text_from_txt_returns_content_as_is(client: TestClient):
    headers = _login_as_admin(client)
    content = "강남구 외식업 소비 트렌드를 분석해주세요.".encode("utf-8")

    response = _upload(client, headers, "요구사항.txt", content, "text/plain")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["extracted_text"] == "강남구 외식업 소비 트렌드를 분석해주세요."
    assert body["filename"] == "요구사항.txt"
    assert body["truncated"] is False


def test_extract_text_from_docx_joins_paragraphs_in_order(client: TestClient):
    docx = pytest.importorskip("docx")
    headers = _login_as_admin(client)

    document = docx.Document()
    document.add_paragraph("첫 번째 문단입니다.")
    document.add_paragraph("두 번째 문단입니다.")
    buffer = io.BytesIO()
    document.save(buffer)

    response = _upload(
        client,
        headers,
        "요구사항.docx",
        buffer.getvalue(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )

    assert response.status_code == 200, response.text
    body = response.json()
    first = body["extracted_text"].index("첫 번째 문단입니다.")
    second = body["extracted_text"].index("두 번째 문단입니다.")
    assert first < second


def test_extract_text_rejects_unsupported_extension(client: TestClient):
    headers = _login_as_admin(client)

    response = _upload(client, headers, "요구사항.hwp", b"dummy", "application/octet-stream")

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "UNSUPPORTED_FILE_TYPE"


def test_extract_text_rejects_file_over_size_limit(client: TestClient, settings):
    headers = _login_as_admin(client)
    oversized = b"a" * (settings.document_upload_max_bytes + 1)

    response = _upload(client, headers, "big.txt", oversized, "text/plain")

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "FILE_TOO_LARGE"


def test_extract_text_rejects_empty_file(client: TestClient):
    headers = _login_as_admin(client)

    response = _upload(client, headers, "empty.txt", b"", "text/plain")

    assert response.status_code == 400, response.text
    assert response.json()["detail"]["code"] == "EMPTY_EXTRACTED_TEXT"


def test_extract_text_truncates_long_text_and_reports_flag(client: TestClient):
    from app.domains.documents.service import MAX_TEXT_LENGTH

    headers = _login_as_admin(client)
    content = ("가" * (MAX_TEXT_LENGTH + 500)).encode("utf-8")

    response = _upload(client, headers, "long.txt", content, "text/plain")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["truncated"] is True
    assert len(body["extracted_text"]) == MAX_TEXT_LENGTH


def test_extract_text_strips_control_characters(client: TestClient):
    headers = _login_as_admin(client)
    content = "앞\x00뒤 정리 확인\n\n\n\n\n마지막 줄".encode("utf-8")

    response = _upload(client, headers, "noisy.txt", content, "text/plain")

    assert response.status_code == 200, response.text
    text = response.json()["extracted_text"]
    assert "\x00" not in text
    assert "\n\n\n" not in text
    assert "앞뒤 정리 확인" in text
