from __future__ import annotations

import csv
import hashlib
import os
from pathlib import Path
from uuid import uuid4

from fastapi import UploadFile

from app.core.config import settings


CSV_MIME_TYPES = {"text/csv", "application/csv", "application/vnd.ms-excel", "text/plain"}


def _root() -> Path:
    return Path(settings.upload_root).resolve()


def resolve_storage_key(storage_key: str) -> Path:
    root = _root()
    path = (root / storage_key).resolve()
    if not path.is_relative_to(root):
        raise ValueError("invalid storage key")
    return path


async def save_upload(run_id: int, upload: UploadFile) -> dict:
    if not upload.filename or Path(upload.filename).suffix.lower() != ".csv":
        raise ValueError("CSV 파일만 업로드할 수 있습니다.")
    if upload.content_type and upload.content_type.lower() not in CSV_MIME_TYPES:
        raise ValueError("CSV Content-Type이 아닙니다.")

    relative = Path("original") / str(run_id) / f"{uuid4().hex}.csv"
    destination = resolve_storage_key(relative.as_posix())
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_suffix(".part")
    digest = hashlib.sha256()
    size = 0
    limit = settings.csv_upload_max_bytes
    try:
        with temporary.open("xb") as output:
            while chunk := await upload.read(1024 * 1024):
                size += len(chunk)
                if size > limit:
                    raise ValueError(f"CSV 파일은 {limit} bytes를 초과할 수 없습니다.")
                digest.update(chunk)
                output.write(chunk)
        if size == 0:
            raise ValueError("빈 CSV 파일은 업로드할 수 없습니다.")
        os.replace(temporary, destination)
    finally:
        if temporary.exists():
            temporary.unlink()
        await upload.close()

    return {
        "storage_key": relative.as_posix(),
        "size_bytes": size,
        "checksum": digest.hexdigest(),
        "original_filename": Path(upload.filename).name,
    }


def read_csv_rows(storage_key: str) -> list[dict[str, str]]:
    path = resolve_storage_key(storage_key)
    with path.open("r", encoding="utf-8-sig", newline="") as source:
        reader = csv.DictReader(source)
        if not reader.fieldnames or any(not field or not field.strip() for field in reader.fieldnames):
            raise ValueError("CSV 헤더가 없거나 비어 있습니다.")
        rows = list(reader)
    if not rows:
        raise ValueError("CSV 데이터 행이 없습니다.")
    return rows


def write_result(run_id: int, content: bytes) -> dict:
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
