from __future__ import annotations

import json
from typing import Any

from sqlmodel import Session, select

from app.core.config import get_project_config_value, set_project_config_value
from app.models.note import utc_now
from app.models.runtime_config import RuntimeConfig


DEFAULT_SCOPE = "user"


def get_runtime_config(session: Session, path: str, default: Any = None, *, scope: str = DEFAULT_SCOPE) -> Any:
    entry = _get_entry(session, path, scope=scope)
    if entry is None:
        return default
    try:
        return json.loads(entry.value_json)
    except json.JSONDecodeError:
        return default


def get_effective_runtime_config(
    session: Session,
    path: str,
    default: Any = None,
    *,
    scope: str = DEFAULT_SCOPE,
    reload_project_config: bool = False,
) -> Any:
    sentinel = object()
    runtime_value = get_runtime_config(session, path, sentinel, scope=scope)
    if runtime_value is not sentinel:
        return runtime_value
    return get_project_config_value(path, default, reload=reload_project_config)


def set_runtime_config(session: Session, path: str, value: Any, *, scope: str = DEFAULT_SCOPE) -> RuntimeConfig:
    entry = _get_entry(session, path, scope=scope)
    serialized = json.dumps(value, ensure_ascii=False, sort_keys=True)
    now = utc_now()
    if entry is None:
        entry = RuntimeConfig(scope=scope, path=path, value_json=serialized, created_at=now, updated_at=now)
    else:
        entry.value_json = serialized
        entry.updated_at = now
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def set_persistent_runtime_config(
    session: Session,
    path: str,
    value: Any,
    *,
    scope: str = DEFAULT_SCOPE,
) -> RuntimeConfig:
    entry = set_runtime_config(session, path, value, scope=scope)
    set_project_config_value(path, value)
    return entry


def _get_entry(session: Session, path: str, *, scope: str) -> RuntimeConfig | None:
    return session.exec(
        select(RuntimeConfig).where(RuntimeConfig.scope == scope, RuntimeConfig.path == path)
    ).first()
