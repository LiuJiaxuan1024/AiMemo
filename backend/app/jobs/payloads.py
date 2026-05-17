import json
from typing import Any


def encode_payload(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def decode_payload(payload: str) -> dict[str, Any]:
    data = json.loads(payload or "{}")
    if not isinstance(data, dict):
        raise ValueError("Job payload must be a JSON object.")
    return data
