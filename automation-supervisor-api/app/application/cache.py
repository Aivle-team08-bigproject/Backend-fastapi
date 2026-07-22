import hashlib
import json


def build_cache_key(stage_name: str, model_name: str, payload: dict) -> str:
    body = json.dumps(
        {"stage_name": stage_name, "model_name": model_name, "payload": payload},
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(body.encode("utf-8")).hexdigest()
