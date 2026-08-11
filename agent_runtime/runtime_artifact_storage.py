"""AgentCore Runtime가 최종 CSV 정본을 S3에 직접 저장하는 경계."""

import asyncio
import base64
import binascii
import hashlib
import os
from uuid import uuid4


def _required_bucket_name() -> str:
    bucket = os.getenv("S3_ARTIFACTS_BUCKET", "").strip()
    if not bucket:
        raise RuntimeError("S3_ARTIFACTS_BUCKET is required for AgentCore artifact storage")
    return bucket


async def store_final_csv_artifact(
    output: dict,
    pipeline_run_id: int,
    *,
    s3_client=None,
) -> dict:
    """검증된 CSV를 S3에 쓰고 base64 본문 대신 참조 메타데이터를 반환한다.

    Runtime execution role의 기본 AWS 자격증명 체인을 사용한다. 따라서 DB 연결 문자열이나
    AWS access key를 Runtime 환경변수 또는 응답 본문에 넣지 않는다.
    """
    artifact = output.get("csv_artifact") if isinstance(output, dict) else None
    if not isinstance(artifact, dict):
        raise ValueError("csv_artifact must be a JSON object")
    encoded = artifact.get("content_base64")
    if not isinstance(encoded, str) or not encoded:
        raise ValueError("csv_artifact.content_base64 must be a non-empty string")
    try:
        content = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError, TypeError) as exc:
        raise ValueError("csv_artifact.content_base64 must be valid base64") from exc
    if not content:
        raise ValueError("csv_artifact content must not be empty")

    checksum = hashlib.sha256(content).hexdigest()
    if artifact.get("byte_size") != len(content):
        raise ValueError("csv_artifact.byte_size does not match decoded content")
    if artifact.get("sha256") != checksum:
        raise ValueError("csv_artifact.sha256 does not match decoded content")

    bucket = _required_bucket_name()
    prefix = os.getenv("S3_ARTIFACTS_PREFIX", "results").strip("/") or "results"
    key = f"{prefix}/{pipeline_run_id}/result-{uuid4().hex}.csv"
    if s3_client is None:
        import boto3

        s3_client = boto3.client("s3", region_name=os.getenv("AWS_REGION"))
    await asyncio.to_thread(
        s3_client.put_object,
        Bucket=bucket,
        Key=key,
        Body=content,
        ContentType="text/csv; charset=utf-8",
        Metadata={"sha256": checksum, "run_id": str(pipeline_run_id)},
    )

    return {
        **output,
        "csv_artifact": {
            "encoding": "utf-8-sig",
            "sha256": checksum,
            "byte_size": len(content),
            "storage_backend": "s3",
            "storage_key": key,
            "mime_type": "text/csv; charset=utf-8",
            "filename": f"pipeline-run-{pipeline_run_id}-result.csv",
        },
    }
