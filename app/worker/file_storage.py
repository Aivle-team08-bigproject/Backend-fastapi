from __future__ import annotations

import hashlib
import os
from pathlib import Path
from uuid import uuid4

from app.core.config import settings


def _root() -> Path:
    return Path(settings.upload_root).resolve()


def resolve_storage_key(storage_key: str) -> Path:
    root = _root()
    path = (root / storage_key).resolve()
    if not path.is_relative_to(root):
        raise ValueError("invalid storage key")
    return path


def write_result(run_id: int, content: bytes) -> dict:
    if settings.artifact_storage_backend == "s3":
        return _write_result_s3(run_id, content)
    return _write_result_local(run_id, content)


def _write_result_local(run_id: int, content: bytes) -> dict:
    relative = Path("results") / str(run_id) / f"result-{uuid4().hex}.csv"
    destination = resolve_storage_key(relative.as_posix())
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".part")
    with temporary.open("xb") as output:
        output.write(content)
    os.replace(temporary, destination)
    return {
        "storage_key": relative.as_posix(),
        "size_bytes": len(content),
        "checksum": hashlib.sha256(content).hexdigest(),
        "mime_type": "text/csv; charset=utf-8",
        "filename": f"pipeline-run-{run_id}-result.csv",
    }


def _write_result_s3(run_id: int, content: bytes) -> dict:
    import boto3

    if not settings.s3_artifacts_bucket.strip():
        raise RuntimeError("S3_ARTIFACTS_BUCKET is required for artifact storage")
    key = f"results/{run_id}/result-{uuid4().hex}.csv"
    checksum = hashlib.sha256(content).hexdigest()
    boto3.client("s3", region_name=settings.aws_region).put_object(
        Bucket=settings.s3_artifacts_bucket,
        Key=key,
        Body=content,
        ContentType="text/csv; charset=utf-8",
        Metadata={"sha256": checksum, "run_id": str(run_id)},
    )
    return {
        "storage_key": key,
        "size_bytes": len(content),
        "checksum": checksum,
        "mime_type": "text/csv; charset=utf-8",
        "filename": f"pipeline-run-{run_id}-result.csv",
    }


def generate_download_url(storage_key: str, filename: str, mime_type: str) -> str | None:
    """S3 백엔드일 때만 presigned GET URL을 만든다. local 백엔드는 None(호출자가 FileResponse로 처리)."""
    if settings.artifact_storage_backend != "s3":
        return None
    if not settings.s3_artifacts_bucket.strip():
        raise RuntimeError("S3_ARTIFACTS_BUCKET is required for artifact downloads")
    import boto3

    client = boto3.client("s3", region_name=settings.aws_region)
    return client.generate_presigned_url(
        "get_object",
        Params={
            "Bucket": settings.s3_artifacts_bucket,
            "Key": storage_key,
            "ResponseContentDisposition": f'attachment; filename="{filename}"',
            "ResponseContentType": mime_type,
        },
        ExpiresIn=settings.s3_artifacts_presign_expires_seconds,
    )
