import asyncio
import base64
import hashlib

from agent_runtime.runtime_artifact_storage import store_final_csv_artifact


class FakeS3Client:
    def __init__(self):
        self.calls = []

    def put_object(self, **kwargs):
        self.calls.append(kwargs)


def test_runtime_stores_csv_and_returns_s3_reference(monkeypatch):
    content = "지역,결제건수\n서울,5\n".encode("utf-8-sig")
    monkeypatch.setenv("S3_ARTIFACTS_BUCKET", "bigproject-dev-artifacts-123456789012")
    client = FakeS3Client()
    output = {
        "csv_artifact": {
            "encoding": "utf-8-sig",
            "content_base64": base64.b64encode(content).decode("ascii"),
            "sha256": hashlib.sha256(content).hexdigest(),
            "byte_size": len(content),
        }
    }

    stored = asyncio.run(store_final_csv_artifact(output, 42, s3_client=client))

    assert client.calls[0]["Bucket"] == "bigproject-dev-artifacts-123456789012"
    assert client.calls[0]["Key"].startswith("results/42/result-")
    assert client.calls[0]["Body"] == content
    assert "content_base64" not in stored["csv_artifact"]
    assert stored["csv_artifact"]["storage_backend"] == "s3"
    assert stored["csv_artifact"]["sha256"] == hashlib.sha256(content).hexdigest()
