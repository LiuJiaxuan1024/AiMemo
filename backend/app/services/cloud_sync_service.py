from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException, status
from sqlmodel import Session, col, select

from app.core.config import settings
from app.models.note import Note, utc_now
from app.models.sync_state import SyncState
from app.rag.hashing import content_hash
from app.schemas.cloud_sync import CloudSyncRunResult, CloudSyncStatusRead
from app.services.cloud_key_service import manifest_key as build_manifest_key
from app.services.cloud_key_service import note_object_key
from app.storage import get_storage_provider
from app.storage.provider import CloudObjectStorageProvider, StorageNotFoundError


SYNC_STATUS_SYNCED = "synced"
SYNC_STATUS_DIRTY = "dirty"
SYNC_STATUS_CONFLICTED = "conflicted"
MANIFEST_CONTENT_TYPE = "application/json; charset=utf-8"
NOTE_CONTENT_TYPE = "application/json; charset=utf-8"


def mark_note_dirty(note: Note) -> None:
    note.local_revision = max(int(note.local_revision or 0) + 1, 1)
    note.sync_status = SYNC_STATUS_DIRTY
    note.sync_conflict_id = ""
    note.last_synced_at = None


def get_sync_status(
    session: Session,
    *,
    provider: CloudObjectStorageProvider | None = None,
) -> CloudSyncStatusRead:
    state = get_or_create_sync_state(session)
    dirty_count = session.exec(select(Note).where(Note.sync_status == SYNC_STATUS_DIRTY)).all()
    conflict_count = session.exec(select(Note).where(Note.sync_status == SYNC_STATUS_CONFLICTED)).all()
    return CloudSyncStatusRead(
        enabled=settings.storage_sync_enabled,
        provider=settings.storage_provider,
        bucket=settings.storage_aliyun_bucket if settings.storage_provider == "aliyun_oss" else "",
        endpoint=settings.storage_aliyun_endpoint if settings.storage_provider == "aliyun_oss" else "",
        user_id=settings.storage_sync_user_id,
        manifest_key=state.manifest_key,
        last_remote_global_revision=state.last_remote_global_revision,
        last_pull_at=state.last_pull_at,
        last_push_at=state.last_push_at,
        dirty_note_count=len(dirty_count),
        conflict_count=len(conflict_count),
        last_error=state.last_error,
    )


def push_once(
    session: Session,
    *,
    provider: CloudObjectStorageProvider | None = None,
) -> CloudSyncRunResult:
    storage = provider or get_storage_provider()
    state = get_or_create_sync_state(session)
    dirty_notes = session.exec(
        select(Note)
        .where(Note.sync_status == SYNC_STATUS_DIRTY)
        .order_by(col(Note.updated_at), col(Note.id))
    ).all()
    if not dirty_notes:
        state.last_push_at = utc_now()
        state.updated_at = utc_now()
        state.last_error = ""
        session.add(state)
        session.commit()
        return CloudSyncRunResult(status="ok", message="No dirty notes to upload.")

    remote_manifest = _load_remote_manifest(storage, state.manifest_key)
    if remote_manifest is None:
        remote_manifest = _empty_manifest(state.user_id)

    uploaded = 0
    conflicts = 0
    for note in dirty_notes:
        note_id = int(note.id or 0)
        remote_note = _manifest_notes(remote_manifest).get(str(note_id))
        if _has_remote_conflict(note, remote_note):
            _mark_note_conflicted(note, remote_note)
            conflicts += 1
            session.add(note)
            continue

        object_key = note.cloud_object_key or note_object_key(state.user_id, note_id)
        note.cloud_object_key = object_key
        next_revision = max(int(note.local_revision or 0), int(note.cloud_revision or 0) + 1, 1)
        note.local_revision = next_revision
        note.cloud_revision = next_revision
        payload = _note_to_cloud_payload(note, revision=next_revision, object_key=object_key)
        storage.put_bytes(
            object_key,
            _json_bytes(payload),
            content_type=NOTE_CONTENT_TYPE,
            metadata={"note_id": str(note_id)},
        )
        _set_manifest_note(remote_manifest, note, revision=next_revision, object_key=object_key)
        note.last_synced_revision = next_revision
        note.sync_status = SYNC_STATUS_SYNCED
        note.sync_conflict_id = ""
        note.last_synced_at = utc_now()
        session.add(note)
        uploaded += 1

    if uploaded:
        remote_manifest["global_revision"] = int(remote_manifest.get("global_revision") or 0) + 1
        remote_manifest["updated_at"] = _iso_now()
        remote_manifest["device_id"] = "local"
        storage.put_bytes(
            state.manifest_key,
            _json_bytes(remote_manifest),
            content_type=MANIFEST_CONTENT_TYPE,
            metadata={"user_id": state.user_id},
        )
        state.last_remote_global_revision = int(remote_manifest.get("global_revision") or 0)

    state.last_push_at = utc_now()
    state.updated_at = utc_now()
    state.last_error = ""
    session.add(state)
    session.commit()
    return CloudSyncRunResult(status="ok", uploaded_note_count=uploaded, conflict_count=conflicts)


def pull_once(
    session: Session,
    *,
    provider: CloudObjectStorageProvider | None = None,
) -> CloudSyncRunResult:
    storage = provider or get_storage_provider()
    state = get_or_create_sync_state(session)
    remote_manifest = _load_remote_manifest(storage, state.manifest_key)
    if remote_manifest is None:
        state.last_pull_at = utc_now()
        state.updated_at = utc_now()
        state.last_error = ""
        session.add(state)
        session.commit()
        return CloudSyncRunResult(status="ok", message="Remote manifest does not exist yet.")

    remote_global = int(remote_manifest.get("global_revision") or 0)
    if remote_global == state.last_remote_global_revision:
        state.last_pull_at = utc_now()
        state.updated_at = utc_now()
        state.last_error = ""
        session.add(state)
        session.commit()
        return CloudSyncRunResult(status="ok", skipped_note_count=len(_manifest_notes(remote_manifest)))

    downloaded = 0
    skipped = 0
    conflicts = 0
    for note_id_text, remote_note in _manifest_notes(remote_manifest).items():
        note_id = _parse_note_id(note_id_text)
        if note_id is None:
            skipped += 1
            continue
        local_note = session.get(Note, note_id)
        remote_revision = int(remote_note.get("revision") or 0)
        if local_note and int(local_note.cloud_revision or 0) >= remote_revision:
            skipped += 1
            continue
        if local_note and local_note.sync_status == SYNC_STATUS_DIRTY:
            _mark_note_conflicted(local_note, remote_note)
            session.add(local_note)
            conflicts += 1
            continue
        object_key = str(remote_note.get("object_key") or note_object_key(state.user_id, note_id))
        try:
            payload = json.loads(storage.get_bytes(object_key).decode("utf-8"))
        except StorageNotFoundError:
            skipped += 1
            continue
        _apply_cloud_note_payload(session, payload, remote_note=remote_note, user_id=state.user_id)
        downloaded += 1

    state.last_remote_global_revision = remote_global
    state.last_pull_at = utc_now()
    state.updated_at = utc_now()
    state.last_error = ""
    session.add(state)
    session.commit()
    return CloudSyncRunResult(
        status="ok",
        downloaded_note_count=downloaded,
        skipped_note_count=skipped,
        conflict_count=conflicts,
    )


def sync_once(
    session: Session,
    *,
    provider: CloudObjectStorageProvider | None = None,
) -> CloudSyncRunResult:
    storage = provider or get_storage_provider()
    pulled = pull_once(session, provider=storage)
    pushed = push_once(session, provider=storage)
    return CloudSyncRunResult(
        status="ok",
        uploaded_note_count=pushed.uploaded_note_count,
        downloaded_note_count=pulled.downloaded_note_count,
        skipped_note_count=pulled.skipped_note_count + pushed.skipped_note_count,
        conflict_count=pulled.conflict_count + pushed.conflict_count,
        message="Sync completed.",
    )


def get_or_create_sync_state(session: Session) -> SyncState:
    user_id = settings.storage_sync_user_id
    key = build_manifest_key(user_id)
    state = session.exec(
        select(SyncState)
        .where(SyncState.provider == settings.storage_provider)
        .where(SyncState.user_id == user_id)
        .where(SyncState.manifest_key == key)
    ).first()
    if state is not None:
        return state
    state = SyncState(
        provider=settings.storage_provider,
        user_id=user_id,
        manifest_key=key,
    )
    session.add(state)
    session.flush()
    return state


def _load_remote_manifest(storage: CloudObjectStorageProvider, key: str) -> dict[str, Any] | None:
    try:
        data = storage.get_bytes(key)
    except StorageNotFoundError:
        return None
    try:
        value = json.loads(data.decode("utf-8"))
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY,
            detail=f"Remote manifest is invalid: {exc}",
        ) from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail="Remote manifest must be an object.")
    value.setdefault("notes", {})
    return value


def _empty_manifest(user_id: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "user_id": user_id,
        "global_revision": 0,
        "updated_at": _iso_now(),
        "device_id": "local",
        "notes": {},
    }


def _manifest_notes(manifest: dict[str, Any]) -> dict[str, Any]:
    notes = manifest.get("notes")
    return notes if isinstance(notes, dict) else {}


def _set_manifest_note(manifest: dict[str, Any], note: Note, *, revision: int, object_key: str) -> None:
    notes = manifest.setdefault("notes", {})
    note_id = str(int(note.id or 0))
    notes[note_id] = {
        "revision": revision,
        "content_hash": note.content_hash,
        "updated_at": _to_iso(note.updated_at),
        "deleted": note.status == "deleted",
        "object_key": object_key,
    }


def _note_to_cloud_payload(note: Note, *, revision: int, object_key: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "id": int(note.id or 0),
        "title": note.title,
        "title_source": note.title_source,
        "content_markdown": note.content_markdown or note.content or "",
        "content_blocks": note.content_blocks or "",
        "content_format": note.content_format or "markdown",
        "content_version": note.content_version or 1,
        "content_hash": note.content_hash,
        "summary": note.summary,
        "tags": _decode_tags(note.tags),
        "status": note.status,
        "deleted_at": _to_iso_or_none(note.deleted_at),
        "created_at": _to_iso(note.created_at),
        "updated_at": _to_iso(note.updated_at),
        "revision": revision,
        "object_key": object_key,
        "attachments": [],
    }


def _apply_cloud_note_payload(
    session: Session,
    payload: dict[str, Any],
    *,
    remote_note: dict[str, Any],
    user_id: str,
) -> None:
    note_id = int(payload["id"])
    remote_revision = int(payload.get("revision") or remote_note.get("revision") or 0)
    note = session.get(Note, note_id)
    if note is None:
        note = Note(
            id=note_id,
            title=str(payload.get("title") or ""),
            content=_payload_markdown(payload),
            content_markdown=_payload_markdown(payload),
        )
    note.title = str(payload.get("title") or "")
    note.title_source = str(payload.get("title_source") or "user")
    note.content = _payload_markdown(payload)
    note.content_markdown = _payload_markdown(payload)
    note.content_blocks = str(payload.get("content_blocks") or "")
    note.content_format = str(payload.get("content_format") or "markdown")
    note.content_version = int(payload.get("content_version") or 1)
    note.content_hash = str(payload.get("content_hash") or content_hash(note.content_markdown.strip()))
    note.summary = str(payload.get("summary") or "")
    note.tags = _encode_tags([str(item) for item in payload.get("tags") or []])
    note.status = str(payload.get("status") or ("deleted" if remote_note.get("deleted") else "active"))
    note.deleted_at = _parse_datetime(payload.get("deleted_at"))
    note.created_at = _parse_datetime(payload.get("created_at")) or note.created_at
    note.updated_at = _parse_datetime(payload.get("updated_at")) or utc_now()
    note.cloud_revision = remote_revision
    note.local_revision = remote_revision
    note.last_synced_revision = remote_revision
    note.sync_status = SYNC_STATUS_SYNCED
    note.sync_conflict_id = ""
    note.cloud_object_key = str(payload.get("object_key") or note_object_key(user_id, note_id))
    note.last_synced_at = utc_now()
    session.add(note)


def _has_remote_conflict(note: Note, remote_note: dict[str, Any] | None) -> bool:
    if not remote_note:
        return False
    remote_revision = int(remote_note.get("revision") or 0)
    return remote_revision > int(note.cloud_revision or 0) and note.sync_status == SYNC_STATUS_DIRTY


def _mark_note_conflicted(note: Note, remote_note: dict[str, Any] | None) -> None:
    remote_revision = int((remote_note or {}).get("revision") or 0)
    note.cloud_revision = max(int(note.cloud_revision or 0), remote_revision)
    note.sync_status = SYNC_STATUS_CONFLICTED
    note.sync_conflict_id = f"note:{note.id}:remote:{remote_revision}"


def _json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _payload_markdown(payload: dict[str, Any]) -> str:
    return str(payload.get("content_markdown") or payload.get("content") or "").strip()


def _encode_tags(tags: list[str]) -> str:
    return ",".join(tag.strip() for tag in tags if tag.strip())


def _decode_tags(tags: str) -> list[str]:
    return [item for item in tags.split(",") if item] if tags else []


def _parse_note_id(value: str) -> int | None:
    try:
        return int(value)
    except ValueError:
        return None


def _iso_now() -> str:
    return _to_iso(datetime.now(timezone.utc))


def _to_iso(value: datetime) -> str:
    normalized = value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return normalized.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _to_iso_or_none(value: datetime | None) -> str | None:
    return _to_iso(value) if value else None


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
