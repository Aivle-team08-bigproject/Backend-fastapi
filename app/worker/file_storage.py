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
