from __future__ import annotations


def normalize_user_id(user_id: str) -> str:
    normalized = user_id.strip().strip("/")
    return normalized or "local-user"


def manifest_key(user_id: str) -> str:
    return f"users/{normalize_user_id(user_id)}/sync/manifest.json"


def note_object_key(user_id: str, note_id: int) -> str:
    return f"users/{normalize_user_id(user_id)}/sync/notes/{note_id}.json"
