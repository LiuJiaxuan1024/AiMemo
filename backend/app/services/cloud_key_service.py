from __future__ import annotations


def normalize_user_id(user_id: str) -> str:
    normalized = user_id.strip().strip("/")
    return normalized or "local-user"


def manifest_key(user_id: str) -> str:
    return f"users/{normalize_user_id(user_id)}/sync/manifest.json"


def global_manifest_key(user_id: str) -> str:
    return f"users/{normalize_user_id(user_id)}/sync/global_manifest.json"


def domain_manifest_key(user_id: str, domain: str) -> str:
    return f"users/{normalize_user_id(user_id)}/sync/domains/{_normalize_segment(domain)}_manifest.json"


def note_object_key(user_id: str, note_id: int) -> str:
    return f"users/{normalize_user_id(user_id)}/sync/notes/{note_id}.json"


def domain_object_key(user_id: str, domain: str, entity_id: str | int) -> str:
    return f"users/{normalize_user_id(user_id)}/sync/{_normalize_segment(domain)}/{_normalize_segment(str(entity_id))}.json"


def cloud_object_key(user_id: str, category: str, *parts: str | int) -> str:
    suffix = "/".join(_normalize_segment(str(part)) for part in parts)
    return f"users/{normalize_user_id(user_id)}/objects/{_normalize_segment(category)}/{suffix}"


def backup_object_key(user_id: str, name: str) -> str:
    return f"users/{normalize_user_id(user_id)}/backups/{_normalize_segment(name)}"


def _normalize_segment(value: str) -> str:
    cleaned = value.strip().strip("/").replace("\\", "/")
    cleaned = cleaned.replace("..", "")
    return cleaned or "default"
