from __future__ import annotations

import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import tempfile
from typing import Any, Iterable
import zipfile
from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.database import engine
from app.jobs.models import JobStatus
from app.models.chat_attachment import ChatAttachment, ChatAttachmentDerivative
from app.models.chat_message import ChatMessage
from app.models.conversation import Conversation
from app.models.job import Job
from app.models.knowledge import ConversationKnowledgeMount, KnowledgeDocument, KnowledgeSpace
from app.models.long_term_memory import LongTermMemory
from app.models.note import Note, utc_now
from app.models.runtime_config import RuntimeConfig
from app.models.sync_metadata import SyncConflict, SyncDevice, SyncItem
from app.models.sync_state import SyncState
from app.models.voice_profile import VoiceProfile
from app.rag.hashing import content_hash
from app.schemas.cloud_sync import (
    CloudSyncBackupCreateResult,
    CloudSyncBackupRead,
    CloudSyncConflictRead,
    CloudSyncDomainRunResult,
    CloudSyncDomainStatus,
    CloudSyncRunResult,
    CloudSyncStatusRead,
)
from app.services.cloud_key_service import (
    backup_object_key,
    cloud_object_key,
    domain_manifest_key,
    domain_object_key,
    global_manifest_key,
    manifest_key as legacy_manifest_key,
    note_object_key,
)
from app.storage import get_storage_provider
from app.storage.provider import CloudObjectStorageProvider, StorageNotFoundError


SYNC_STATUS_SYNCED = "synced"
SYNC_STATUS_DIRTY = "dirty"
SYNC_STATUS_CONFLICTED = "conflicted"
SYNC_STATUS_DELETED = "deleted"
CONFLICT_STATUS_OPEN = "open"
MANIFEST_CONTENT_TYPE = "application/json; charset=utf-8"
JSON_CONTENT_TYPE = "application/json; charset=utf-8"
BACKUP_CONTENT_TYPE = "application/octet-stream"
SYNC_DOMAINS = ("notes", "conversations", "memories", "config", "knowledge")
CONFIG_SYNC_PATH_PREFIXES = ("elf.voice.", "models.agent_chat.", "models.planner.")
CONFIG_SYNC_DENY_KEYWORDS = ("key", "secret", "token", "password", "credential")


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
    dirty_note_count = len(session.exec(select(Note).where(Note.sync_status == SYNC_STATUS_DIRTY)).all())
    conflict_count = len(_open_conflicts(session))
    domains = [_domain_status(session, domain) for domain in SYNC_DOMAINS]
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
        dirty_note_count=dirty_note_count,
        conflict_count=conflict_count,
        last_error=state.last_error,
        domains=domains,
    )


def push_once(
    session: Session,
    *,
    provider: CloudObjectStorageProvider | None = None,
    domains: Iterable[str] | None = None,
) -> CloudSyncRunResult:
    storage = provider or get_storage_provider()
    selected = _normalize_domains(domains)
    state = get_or_create_sync_state(session)
    _record_device(session, pushed=True)
    results = [_push_domain(session, storage, domain) for domain in selected]
    _write_global_manifest(session, storage, selected)
    state.last_push_at = utc_now()
    state.last_error = ""
    state.updated_at = utc_now()
    state.last_remote_global_revision = max(
        [state.last_remote_global_revision, *[result.uploaded_count for result in results]]
    )
    session.add(state)
    session.commit()
    return _aggregate_result("ok", results, message="Push completed.")


def pull_once(
    session: Session,
    *,
    provider: CloudObjectStorageProvider | None = None,
    domains: Iterable[str] | None = None,
) -> CloudSyncRunResult:
    storage = provider or get_storage_provider()
    selected = _normalize_domains(domains)
    state = get_or_create_sync_state(session)
    _record_device(session, pulled=True)
    _migrate_legacy_note_manifest_if_needed(storage, state.user_id)
    results = [_pull_domain(session, storage, domain) for domain in selected]
    state.last_pull_at = utc_now()
    state.last_error = ""
    state.updated_at = utc_now()
    session.add(state)
    session.commit()
    return _aggregate_result("ok", results, message="Pull completed.")


def sync_once(
    session: Session,
    *,
    provider: CloudObjectStorageProvider | None = None,
    domains: Iterable[str] | None = None,
) -> CloudSyncRunResult:
    storage = provider or get_storage_provider()
    pulled = pull_once(session, provider=storage, domains=domains)
    pushed = push_once(session, provider=storage, domains=domains)
    merged: dict[str, CloudSyncDomainRunResult] = {}
    for result in [*pulled.domains, *pushed.domains]:
        current = merged.setdefault(result.domain, CloudSyncDomainRunResult(domain=result.domain))
        current.uploaded_count += result.uploaded_count
        current.downloaded_count += result.downloaded_count
        current.skipped_count += result.skipped_count
        current.conflict_count += result.conflict_count
        current.error_count += result.error_count
    return _aggregate_result("ok", list(merged.values()), message="Sync completed.")


def sync_domain_once(
    session: Session,
    domain: str,
    *,
    provider: CloudObjectStorageProvider | None = None,
) -> CloudSyncRunResult:
    return sync_once(session, provider=provider, domains=[domain])


def get_or_create_sync_state(session: Session) -> SyncState:
    user_id = settings.storage_sync_user_id
    key = global_manifest_key(user_id)
    state = session.exec(
        select(SyncState)
        .where(SyncState.provider == settings.storage_provider)
        .where(SyncState.user_id == user_id)
        .where(SyncState.manifest_key == key)
    ).first()
    if state is not None:
        return state
    legacy_state = session.exec(
        select(SyncState)
        .where(SyncState.provider == settings.storage_provider)
        .where(SyncState.user_id == user_id)
        .where(SyncState.manifest_key == legacy_manifest_key(user_id))
    ).first()
    state = SyncState(
        provider=settings.storage_provider,
        user_id=user_id,
        manifest_key=key,
        last_remote_global_revision=int(legacy_state.last_remote_global_revision or 0) if legacy_state else 0,
        last_pull_at=legacy_state.last_pull_at if legacy_state else None,
        last_push_at=legacy_state.last_push_at if legacy_state else None,
    )
    session.add(state)
    session.flush()
    return state


def list_domain_statuses(session: Session) -> list[CloudSyncDomainStatus]:
    get_or_create_sync_state(session)
    return [_domain_status(session, domain) for domain in SYNC_DOMAINS]


def list_conflicts(session: Session) -> list[CloudSyncConflictRead]:
    return [_conflict_read(conflict) for conflict in _open_conflicts(session)]


def resolve_conflict(session: Session, conflict_id: int, *, resolution: str = "keep_both") -> CloudSyncConflictRead:
    conflict = session.get(SyncConflict, conflict_id)
    if conflict is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Sync conflict not found")
    normalized = resolution.strip() or "keep_both"
    if normalized != "keep_both":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "UNSUPPORTED_CONFLICT_RESOLUTION", "message": "当前仅支持 keep_both。"},
        )
    conflict.status = "resolved"
    conflict.resolution = normalized
    conflict.updated_at = utc_now()
    if conflict.domain == "notes":
        note = session.get(Note, _parse_int(conflict.entity_id))
        if note is not None and note.sync_status == SYNC_STATUS_CONFLICTED:
            note.sync_status = SYNC_STATUS_DIRTY
            note.sync_conflict_id = ""
            mark_note_dirty(note)
            session.add(note)
    item = _get_sync_item(session, conflict.domain, conflict.entity_id)
    if item is not None and item.status == SYNC_STATUS_CONFLICTED:
        item.status = SYNC_STATUS_DIRTY
        item.updated_at = utc_now()
        session.add(item)
    session.add(conflict)
    session.commit()
    session.refresh(conflict)
    return _conflict_read(conflict)


def list_backups(provider: CloudObjectStorageProvider | None = None) -> list[CloudSyncBackupRead]:
    storage = provider or get_storage_provider()
    user_id = settings.storage_sync_user_id
    prefix = f"users/{user_id}/backups/"
    objects = storage.list_objects(prefix)
    return [
        CloudSyncBackupRead(
            key=item.key,
            name=item.key.rsplit("/", 1)[-1],
            size_bytes=item.size_bytes,
            last_modified=item.last_modified,
        )
        for item in objects
    ]


def create_backup(provider: CloudObjectStorageProvider | None = None) -> CloudSyncBackupCreateResult:
    passphrase = _backup_passphrase()
    if not passphrase:
        return CloudSyncBackupCreateResult(status="disabled", message="AIMEMO_BACKUP_PASSPHRASE is not configured.")
    if not settings.database_url.startswith("sqlite:///"):
        return CloudSyncBackupCreateResult(status="disabled", message="Only SQLite backup is supported.")
    storage = provider or get_storage_provider()
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    name = f"{timestamp}.aimemo-backup"
    key = backup_object_key(settings.storage_sync_user_id, name)
    with tempfile.TemporaryDirectory(prefix="aimemo-backup-") as tmp:
        tmp_path = Path(tmp)
        db_path = Path(settings.database_url.replace("sqlite:///", "", 1)).resolve()
        snapshot_path = tmp_path / "ai_note.db"
        with engine.begin() as connection:
            connection.exec_driver_sql("PRAGMA wal_checkpoint(TRUNCATE)")
        shutil.copy2(db_path, snapshot_path)
        archive_path = tmp_path / "backup.zip"
        with zipfile.ZipFile(archive_path, mode="w", compression=zipfile.ZIP_DEFLATED) as archive:
            archive.write(snapshot_path, arcname="ai_note.db")
        data = _encrypt_backup_bytes(archive_path.read_bytes(), passphrase=passphrase)
    metadata = storage.put_bytes(key, data, content_type=BACKUP_CONTENT_TYPE, metadata={"kind": "aimemo-backup"})
    return CloudSyncBackupCreateResult(status="ok", key=key, size_bytes=metadata.size_bytes)


def _push_domain(session: Session, storage: CloudObjectStorageProvider, domain: str) -> CloudSyncDomainRunResult:
    manifest = _load_domain_manifest(storage, domain) or _empty_domain_manifest(domain)
    uploaded = 0
    conflicts = 0
    for entity_id, payload in _local_domain_payloads(session, domain):
        entity_id_text = str(entity_id)
        item = _get_or_create_sync_item(session, domain, entity_id_text)
        local_hash = _hash_payload(payload)
        if item.content_hash == local_hash and item.status == SYNC_STATUS_SYNCED:
            continue
        remote_entry = _manifest_entities(manifest).get(entity_id_text)
        if _has_item_remote_conflict(item, remote_entry):
            _record_conflict(session, domain, entity_id_text, item, remote_entry)
            conflicts += 1
            continue
        object_key = item.object_key or _domain_object_key(domain, entity_id_text)
        payload["schema_version"] = 1
        payload["id"] = entity_id
        payload["domain"] = domain
        revision = max(int(item.local_revision or 0), int(item.cloud_revision or 0) + 1, 1)
        payload["revision"] = revision
        payload["object_key"] = object_key
        _upload_payload_assets(storage, domain, payload)
        storage.put_bytes(object_key, _json_bytes(payload), content_type=JSON_CONTENT_TYPE, metadata={"domain": domain})
        _set_manifest_entity(manifest, entity_id_text, payload, revision=revision, object_key=object_key, content_hash=local_hash)
        item.object_key = object_key
        item.local_revision = revision
        item.cloud_revision = revision
        item.last_synced_revision = revision
        item.content_hash = local_hash
        item.status = SYNC_STATUS_SYNCED
        item.last_synced_at = utc_now()
        item.updated_at = utc_now()
        session.add(item)
        _mark_domain_entity_synced(session, domain, entity_id_text, revision, object_key)
        uploaded += 1
    if uploaded:
        manifest["global_revision"] = int(manifest.get("global_revision") or 0) + 1
        manifest["updated_at"] = _iso_now()
        manifest["device_id"] = _device_id()
        _put_domain_manifest(storage, domain, manifest)
        if domain == "notes":
            _put_legacy_note_manifest(storage, manifest)
    return CloudSyncDomainRunResult(domain=domain, uploaded_count=uploaded, conflict_count=conflicts)


def _pull_domain(session: Session, storage: CloudObjectStorageProvider, domain: str) -> CloudSyncDomainRunResult:
    manifest = _load_domain_manifest(storage, domain)
    if manifest is None:
        return CloudSyncDomainRunResult(domain=domain, message="Remote manifest does not exist.")
    downloaded = 0
    skipped = 0
    conflicts = 0
    for entity_id, remote_entry in _manifest_entities(manifest).items():
        item = _get_sync_item(session, domain, entity_id)
        if item is None:
            item = _create_sync_item(session, domain, entity_id, status=_initial_pull_item_status(session, domain, entity_id))
        remote_revision = int(remote_entry.get("revision") or 0)
        if int(item.cloud_revision or 0) >= remote_revision:
            skipped += 1
            continue
        if item.status == SYNC_STATUS_DIRTY or _entity_is_local_dirty(session, domain, entity_id):
            _record_conflict(session, domain, entity_id, item, remote_entry)
            conflicts += 1
            continue
        object_key = str(remote_entry.get("object_key") or _domain_object_key(domain, entity_id))
        try:
            payload = json.loads(storage.get_bytes(object_key).decode("utf-8"))
        except StorageNotFoundError:
            skipped += 1
            continue
        _download_payload_assets(storage, domain, payload)
        _apply_domain_payload(session, domain, payload)
        item.object_key = object_key
        item.cloud_revision = remote_revision
        item.local_revision = remote_revision
        item.last_synced_revision = remote_revision
        item.content_hash = str(remote_entry.get("content_hash") or _hash_payload(payload))
        item.status = SYNC_STATUS_SYNCED
        item.last_synced_at = utc_now()
        item.updated_at = utc_now()
        session.add(item)
        downloaded += 1
    return CloudSyncDomainRunResult(domain=domain, downloaded_count=downloaded, skipped_count=skipped, conflict_count=conflicts)


def _local_domain_payloads(session: Session, domain: str) -> list[tuple[str, dict[str, Any]]]:
    if domain == "notes":
        notes = session.exec(select(Note).where(Note.status != SYNC_STATUS_DELETED).order_by(col(Note.updated_at), col(Note.id))).all()
        return [(str(note.id), _note_payload(note)) for note in notes if note.id is not None]
    if domain == "conversations":
        conversations = session.exec(select(Conversation).where(Conversation.status != "deleted").order_by(Conversation.updated_at, Conversation.id)).all()
        return [(str(conversation.id), _conversation_payload(session, conversation)) for conversation in conversations if conversation.id is not None]
    if domain == "memories":
        memories = session.exec(select(LongTermMemory).where(LongTermMemory.status != "deleted").order_by(LongTermMemory.updated_at, LongTermMemory.id)).all()
        voice_profiles = session.exec(select(VoiceProfile).where(VoiceProfile.status != "deleted").order_by(VoiceProfile.updated_at, VoiceProfile.id)).all()
        return [
            *[(f"long_term_memory:{memory.id}", _long_term_memory_payload(memory)) for memory in memories if memory.id is not None],
            *[(f"voice_profile:{profile.id}", _voice_profile_payload(profile)) for profile in voice_profiles if profile.id is not None],
        ]
    if domain == "config":
        configs = session.exec(select(RuntimeConfig).order_by(RuntimeConfig.updated_at, RuntimeConfig.id)).all()
        return [(f"runtime_config:{config.id}", _runtime_config_payload(config)) for config in configs if config.id is not None and _is_syncable_config(config.path)]
    if domain == "knowledge":
        spaces = session.exec(select(KnowledgeSpace).where(KnowledgeSpace.status != "deleted").order_by(KnowledgeSpace.updated_at, KnowledgeSpace.id)).all()
        return [(str(space.id), _knowledge_space_payload(session, space)) for space in spaces if space.id is not None]
    return []


def _apply_domain_payload(session: Session, domain: str, payload: dict[str, Any]) -> None:
    if domain == "notes":
        _apply_note_payload(session, payload)
    elif domain == "conversations":
        _apply_conversation_payload(session, payload)
    elif domain == "memories":
        _apply_memory_payload(session, payload)
    elif domain == "config":
        _apply_config_payload(session, payload)
    elif domain == "knowledge":
        _apply_knowledge_payload(session, payload)


def _note_payload(note: Note) -> dict[str, Any]:
    return {
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
    }


def _apply_note_payload(session: Session, payload: dict[str, Any]) -> None:
    note_id = int(payload["id"])
    note = session.get(Note, note_id) or Note(id=note_id, title="", content="")
    note.title = str(payload.get("title") or "")
    note.title_source = str(payload.get("title_source") or "user")
    note.content = str(payload.get("content_markdown") or payload.get("content") or "")
    note.content_markdown = note.content
    note.content_blocks = str(payload.get("content_blocks") or "")
    note.content_format = str(payload.get("content_format") or "markdown")
    note.content_version = int(payload.get("content_version") or 1)
    note.content_hash = str(payload.get("content_hash") or content_hash(note.content.strip()))
    note.summary = str(payload.get("summary") or "")
    note.tags = _encode_tags([str(item) for item in payload.get("tags") or []])
    note.status = str(payload.get("status") or "active")
    note.deleted_at = _parse_datetime(payload.get("deleted_at"))
    note.created_at = _parse_datetime(payload.get("created_at")) or note.created_at
    note.updated_at = _parse_datetime(payload.get("updated_at")) or utc_now()
    note.cloud_revision = int(payload.get("revision") or note.cloud_revision or 0)
    note.local_revision = note.cloud_revision
    note.last_synced_revision = note.cloud_revision
    note.sync_status = SYNC_STATUS_SYNCED
    note.sync_conflict_id = ""
    note.cloud_object_key = str(payload.get("object_key") or "")
    note.last_synced_at = utc_now()
    session.add(note)


def _conversation_payload(session: Session, conversation: Conversation) -> dict[str, Any]:
    conversation_id = int(conversation.id or 0)
    messages = session.exec(
        select(ChatMessage).where(ChatMessage.conversation_id == conversation_id).order_by(ChatMessage.created_at, ChatMessage.id)
    ).all()
    attachments = session.exec(
        select(ChatAttachment).where(ChatAttachment.conversation_id == conversation_id).order_by(ChatAttachment.created_at, ChatAttachment.id)
    ).all()
    attachment_ids = [attachment.id for attachment in attachments if attachment.id is not None]
    derivatives = session.exec(
        select(ChatAttachmentDerivative).where(col(ChatAttachmentDerivative.attachment_id).in_(attachment_ids)).order_by(ChatAttachmentDerivative.created_at, ChatAttachmentDerivative.id)
    ).all() if attachment_ids else []
    return {
        "conversation": _model_payload(conversation, exclude={"active_task"}),
        "messages": [_model_payload(message, exclude={"checkpoint_id"}) for message in messages],
        "attachments": [_attachment_payload(attachment) for attachment in attachments],
        "attachment_derivatives": [_model_payload(derivative) for derivative in derivatives],
        "updated_at": _to_iso(conversation.updated_at),
    }


def _attachment_payload(attachment: ChatAttachment) -> dict[str, Any]:
    data = _model_payload(attachment)
    data["cloud_object_key"] = cloud_object_key(
        settings.storage_sync_user_id,
        "chat_attachments",
        attachment.conversation_id,
        attachment.id or 0,
        f"{attachment.sha256[:16]}-{Path(attachment.original_name or 'attachment').name}",
    )
    return data


def _apply_conversation_payload(session: Session, payload: dict[str, Any]) -> None:
    conversation_data = payload.get("conversation") or {}
    conversation_id = int(conversation_data.get("id") or payload["id"])
    conversation = session.get(Conversation, conversation_id) or Conversation(id=conversation_id)
    _assign_fields(conversation, conversation_data, exclude={"id", "active_task"})
    session.add(conversation)
    for message_data in payload.get("messages") or []:
        message_id = int(message_data.get("id") or 0)
        if message_id <= 0:
            continue
        message = session.get(ChatMessage, message_id) or ChatMessage(id=message_id, conversation_id=conversation_id, role="assistant", content="")
        _assign_fields(message, message_data, exclude={"id", "checkpoint_id"})
        message.conversation_id = conversation_id
        session.add(message)
    for attachment_data in payload.get("attachments") or []:
        attachment_id = int(attachment_data.get("id") or 0)
        if attachment_id <= 0:
            continue
        attachment = session.get(ChatAttachment, attachment_id) or ChatAttachment(id=attachment_id, conversation_id=conversation_id)
        _assign_fields(attachment, attachment_data, exclude={"id", "cloud_object_key"})
        attachment.conversation_id = conversation_id
        session.add(attachment)
    for derivative_data in payload.get("attachment_derivatives") or []:
        derivative_id = int(derivative_data.get("id") or 0)
        if derivative_id <= 0:
            continue
        derivative = session.get(ChatAttachmentDerivative, derivative_id) or ChatAttachmentDerivative(id=derivative_id, attachment_id=0)
        _assign_fields(derivative, derivative_data, exclude={"id"})
        session.add(derivative)


def _long_term_memory_payload(memory: LongTermMemory) -> dict[str, Any]:
    return {"kind": "long_term_memory", "record": _model_payload(memory), "updated_at": _to_iso(memory.updated_at)}


def _voice_profile_payload(profile: VoiceProfile) -> dict[str, Any]:
    record = _model_payload(profile)
    record["remote_voice_id"] = ""
    return {"kind": "voice_profile", "record": record, "updated_at": _to_iso(profile.updated_at)}


def _runtime_config_payload(config: RuntimeConfig) -> dict[str, Any]:
    return {"kind": "runtime_config", "record": _model_payload(config), "updated_at": _to_iso(config.updated_at)}


def _apply_memory_payload(session: Session, payload: dict[str, Any]) -> None:
    record = payload.get("record") or {}
    if payload.get("kind") == "long_term_memory":
        item_id = int(record.get("id") or 0)
        item = session.get(LongTermMemory, item_id) or LongTermMemory(id=item_id, content="", content_hash="")
        _assign_fields(item, record, exclude={"id"})
        session.add(item)
    elif payload.get("kind") == "voice_profile":
        item_id = int(record.get("id") or 0)
        item = session.get(VoiceProfile, item_id) or VoiceProfile(id=item_id)
        _assign_fields(item, record, exclude={"id", "remote_voice_id"})
        session.add(item)


def _apply_config_payload(session: Session, payload: dict[str, Any]) -> None:
    record = payload.get("record") or {}
    path = str(record.get("path") or "")
    if not _is_syncable_config(path):
        return
    item_id = int(record.get("id") or 0)
    item = session.get(RuntimeConfig, item_id) if item_id else None
    if item is None:
        item = session.exec(select(RuntimeConfig).where(RuntimeConfig.scope == str(record.get("scope") or "user"), RuntimeConfig.path == path)).first()
    if item is None:
        item = RuntimeConfig(scope=str(record.get("scope") or "user"), path=path)
    _assign_fields(item, record, exclude={"id"})
    session.add(item)


def _knowledge_space_payload(session: Session, space: KnowledgeSpace) -> dict[str, Any]:
    space_id = int(space.id or 0)
    documents = session.exec(
        select(KnowledgeDocument).where(KnowledgeDocument.space_id == space_id, KnowledgeDocument.status != "deleted").order_by(KnowledgeDocument.updated_at, KnowledgeDocument.id)
    ).all()
    mounts = session.exec(
        select(ConversationKnowledgeMount).where(ConversationKnowledgeMount.space_id == space_id).order_by(ConversationKnowledgeMount.created_at, ConversationKnowledgeMount.id)
    ).all()
    return {
        "space": _model_payload(space),
        "documents": [_knowledge_document_payload(document) for document in documents],
        "mounts": [_model_payload(mount) for mount in mounts],
        "updated_at": _to_iso(space.updated_at),
    }


def _knowledge_document_payload(document: KnowledgeDocument) -> dict[str, Any]:
    data = _model_payload(document)
    if document.storage_path:
        data["cloud_object_key"] = cloud_object_key(
            settings.storage_sync_user_id,
            "knowledge_sources",
            document.space_id,
            document.id or 0,
            Path(document.original_filename or "document").name,
        )
    return data


def _apply_knowledge_payload(session: Session, payload: dict[str, Any]) -> None:
    space_data = payload.get("space") or {}
    space_id = int(space_data.get("id") or payload["id"])
    space = session.get(KnowledgeSpace, space_id) or KnowledgeSpace(id=space_id, name="")
    _assign_fields(space, space_data, exclude={"id"})
    session.add(space)
    for document_data in payload.get("documents") or []:
        document_id = int(document_data.get("id") or 0)
        if document_id <= 0:
            continue
        document = session.get(KnowledgeDocument, document_id) or KnowledgeDocument(id=document_id, space_id=space_id, title="")
        _assign_fields(document, document_data, exclude={"id", "cloud_object_key"})
        document.space_id = space_id
        if document.status in {"completed", "failed"}:
            document.status = "pending"
            document.error_code = None
            document.error_message = None
        session.add(document)
        _enqueue_knowledge_rebuild_if_needed(session, document)
    for mount_data in payload.get("mounts") or []:
        mount_id = int(mount_data.get("id") or 0)
        if mount_id <= 0:
            continue
        mount = session.get(ConversationKnowledgeMount, mount_id) or ConversationKnowledgeMount(id=mount_id, conversation_id=0, space_id=space_id)
        _assign_fields(mount, mount_data, exclude={"id"})
        mount.space_id = space_id
        session.add(mount)


def _upload_payload_assets(storage: CloudObjectStorageProvider, domain: str, payload: dict[str, Any]) -> None:
    if domain == "conversations":
        for attachment in payload.get("attachments") or []:
            path = Path(str(attachment.get("storage_path") or ""))
            key = str(attachment.get("cloud_object_key") or "")
            if key and path.exists() and path.is_file():
                storage.put_bytes(key, path.read_bytes(), content_type=str(attachment.get("mime_type") or "application/octet-stream"))
    if domain == "knowledge":
        for document in payload.get("documents") or []:
            key = str(document.get("cloud_object_key") or "")
            storage_path = str(document.get("storage_path") or "")
            if not key or not storage_path:
                continue
            path = Path(__file__).resolve().parents[2] / "data" / "knowledge" / storage_path
            if path.exists() and path.is_file():
                storage.put_bytes(key, path.read_bytes(), content_type=str(document.get("mime_type") or "application/octet-stream"))


def _download_payload_assets(storage: CloudObjectStorageProvider, domain: str, payload: dict[str, Any]) -> None:
    if domain == "conversations":
        for attachment in payload.get("attachments") or []:
            key = str(attachment.get("cloud_object_key") or "")
            if not key:
                continue
            target = Path(str(attachment.get("storage_path") or ""))
            if not target.is_absolute():
                target = Path(settings.attachments_storage_dir).expanduser() / "conversations" / str(attachment.get("conversation_id")) / target.name
                attachment["storage_path"] = str(target.resolve())
            if not target.exists():
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(storage.get_bytes(key))
                except StorageNotFoundError:
                    pass
    if domain == "knowledge":
        root = Path(__file__).resolve().parents[2] / "data" / "knowledge"
        for document in payload.get("documents") or []:
            key = str(document.get("cloud_object_key") or "")
            storage_path = str(document.get("storage_path") or "")
            if not key or not storage_path:
                continue
            target = root / storage_path
            if not target.exists():
                try:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_bytes(storage.get_bytes(key))
                except StorageNotFoundError:
                    pass


def _enqueue_knowledge_rebuild_if_needed(session: Session, document: KnowledgeDocument) -> None:
    if document.id is None or not document.storage_path:
        return
    active = session.exec(
        select(Job).where(
            Job.dedupe_key == f"knowledge_ingest:{document.id}:{document.content_hash}",
            col(Job.status).in_({JobStatus.PENDING.value, JobStatus.RUNNING.value}),
        )
    ).first()
    if active is not None:
        return
    try:
        from app.services.knowledge_document_service import enqueue_knowledge_ingest_job

        enqueue_knowledge_ingest_job(session, document)
    except Exception:
        return


def _migrate_legacy_note_manifest_if_needed(storage: CloudObjectStorageProvider, user_id: str) -> None:
    try:
        legacy = json.loads(storage.get_bytes(legacy_manifest_key(user_id)).decode("utf-8"))
    except (StorageNotFoundError, json.JSONDecodeError):
        return
    current = None
    try:
        current = json.loads(storage.get_bytes(domain_manifest_key(user_id, "notes")).decode("utf-8"))
    except (StorageNotFoundError, json.JSONDecodeError):
        current = None
    if current is not None and int(current.get("global_revision") or 0) >= int(legacy.get("global_revision") or 0):
        return
    notes = legacy.get("notes")
    if not isinstance(notes, dict):
        return
    manifest = _empty_domain_manifest("notes")
    manifest["global_revision"] = int(legacy.get("global_revision") or 0)
    manifest["updated_at"] = str(legacy.get("updated_at") or _iso_now())
    for note_id, entry in notes.items():
        if not isinstance(entry, dict):
            continue
        object_key = str(entry.get("object_key") or note_object_key(user_id, int(note_id)))
        manifest["entities"][str(note_id)] = {
            "revision": int(entry.get("revision") or 0),
            "content_hash": str(entry.get("content_hash") or ""),
            "updated_at": str(entry.get("updated_at") or _iso_now()),
            "deleted": bool(entry.get("deleted") or False),
            "object_key": object_key,
        }
    storage.put_bytes(domain_manifest_key(user_id, "notes"), _json_bytes(manifest), content_type=MANIFEST_CONTENT_TYPE)


def _write_global_manifest(session: Session, storage: CloudObjectStorageProvider, domains: Iterable[str]) -> None:
    user_id = settings.storage_sync_user_id
    payload = {
        "schema_version": 1,
        "user_id": user_id,
        "global_revision": int(datetime.now(timezone.utc).timestamp()),
        "updated_at": _iso_now(),
        "device_id": _device_id(),
        "domains": {
            domain: {
                "manifest_key": domain_manifest_key(user_id, domain),
                "updated_at": _iso_now(),
            }
            for domain in domains
        },
    }
    storage.put_bytes(global_manifest_key(user_id), _json_bytes(payload), content_type=MANIFEST_CONTENT_TYPE)


def _load_domain_manifest(storage: CloudObjectStorageProvider, domain: str) -> dict[str, Any] | None:
    try:
        value = json.loads(storage.get_bytes(domain_manifest_key(settings.storage_sync_user_id, domain)).decode("utf-8"))
    except StorageNotFoundError:
        return None
    except Exception as exc:
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Remote {domain} manifest is invalid: {exc}") from exc
    if not isinstance(value, dict):
        raise HTTPException(status_code=status.HTTP_502_BAD_GATEWAY, detail=f"Remote {domain} manifest must be an object.")
    value.setdefault("entities", {})
    return value


def _put_domain_manifest(storage: CloudObjectStorageProvider, domain: str, manifest: dict[str, Any]) -> None:
    storage.put_bytes(
        domain_manifest_key(settings.storage_sync_user_id, domain),
        _json_bytes(manifest),
        content_type=MANIFEST_CONTENT_TYPE,
        metadata={"domain": domain},
    )


def _put_legacy_note_manifest(storage: CloudObjectStorageProvider, notes_manifest: dict[str, Any]) -> None:
    legacy = {
        "schema_version": 1,
        "user_id": settings.storage_sync_user_id,
        "global_revision": int(notes_manifest.get("global_revision") or 0),
        "updated_at": str(notes_manifest.get("updated_at") or _iso_now()),
        "device_id": str(notes_manifest.get("device_id") or _device_id()),
        "notes": _manifest_entities(notes_manifest),
    }
    storage.put_bytes(
        legacy_manifest_key(settings.storage_sync_user_id),
        _json_bytes(legacy),
        content_type=MANIFEST_CONTENT_TYPE,
        metadata={"domain": "notes", "compat": "legacy"},
    )


def _empty_domain_manifest(domain: str) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "user_id": settings.storage_sync_user_id,
        "domain": domain,
        "global_revision": 0,
        "updated_at": _iso_now(),
        "device_id": _device_id(),
        "entities": {},
    }


def _manifest_entities(manifest: dict[str, Any]) -> dict[str, Any]:
    entities = manifest.get("entities")
    return entities if isinstance(entities, dict) else {}


def _set_manifest_entity(
    manifest: dict[str, Any],
    entity_id: str,
    payload: dict[str, Any],
    *,
    revision: int,
    object_key: str,
    content_hash: str,
) -> None:
    entities = manifest.setdefault("entities", {})
    entities[entity_id] = {
        "revision": revision,
        "content_hash": content_hash,
        "updated_at": str(payload.get("updated_at") or _iso_now()),
        "deleted": payload.get("status") == SYNC_STATUS_DELETED,
        "object_key": object_key,
        "summary": _payload_summary(payload),
    }


def _get_or_create_sync_item(session: Session, domain: str, entity_id: str) -> SyncItem:
    item = _get_sync_item(session, domain, entity_id)
    if item is not None:
        return item
    return _create_sync_item(session, domain, entity_id, status=SYNC_STATUS_DIRTY)


def _create_sync_item(session: Session, domain: str, entity_id: str, *, status: str) -> SyncItem:
    item = SyncItem(
        provider=settings.storage_provider,
        user_id=settings.storage_sync_user_id,
        domain=domain,
        entity_id=entity_id,
        object_key=_domain_object_key(domain, entity_id),
        status=status,
    )
    session.add(item)
    session.flush()
    return item


def _initial_pull_item_status(session: Session, domain: str, entity_id: str) -> str:
    return SYNC_STATUS_DIRTY if _entity_is_local_dirty(session, domain, entity_id) else SYNC_STATUS_SYNCED


def _entity_is_local_dirty(session: Session, domain: str, entity_id: str) -> bool:
    if domain == "notes":
        note = session.get(Note, _parse_int(entity_id))
        if note is not None and note.sync_status == SYNC_STATUS_DIRTY:
            return True
    return False


def _get_sync_item(session: Session, domain: str, entity_id: str) -> SyncItem | None:
    return session.exec(
        select(SyncItem)
        .where(SyncItem.provider == settings.storage_provider)
        .where(SyncItem.user_id == settings.storage_sync_user_id)
        .where(SyncItem.domain == domain)
        .where(SyncItem.entity_id == entity_id)
    ).first()


def _has_item_remote_conflict(item: SyncItem, remote_entry: dict[str, Any] | None) -> bool:
    if not remote_entry:
        return False
    remote_revision = int(remote_entry.get("revision") or 0)
    return remote_revision > int(item.cloud_revision or 0) and item.status == SYNC_STATUS_DIRTY


def _record_conflict(
    session: Session,
    domain: str,
    entity_id: str,
    item: SyncItem,
    remote_entry: dict[str, Any] | None,
) -> None:
    remote_revision = int((remote_entry or {}).get("revision") or 0)
    existing = session.exec(
        select(SyncConflict)
        .where(SyncConflict.provider == settings.storage_provider)
        .where(SyncConflict.user_id == settings.storage_sync_user_id)
        .where(SyncConflict.domain == domain)
        .where(SyncConflict.entity_id == entity_id)
        .where(SyncConflict.remote_revision == remote_revision)
    ).first()
    if existing is None:
        existing = SyncConflict(
            provider=settings.storage_provider,
            user_id=settings.storage_sync_user_id,
            domain=domain,
            entity_id=entity_id,
            local_revision=int(item.local_revision or 0),
            remote_revision=remote_revision,
            local_summary=item.content_hash,
            remote_summary=str((remote_entry or {}).get("summary") or (remote_entry or {}).get("content_hash") or ""),
            remote_object_key=str((remote_entry or {}).get("object_key") or ""),
        )
    existing.status = CONFLICT_STATUS_OPEN
    existing.updated_at = utc_now()
    session.add(existing)
    item.cloud_revision = max(int(item.cloud_revision or 0), remote_revision)
    item.status = SYNC_STATUS_CONFLICTED
    item.updated_at = utc_now()
    session.add(item)
    if domain == "notes":
        note = session.get(Note, _parse_int(entity_id))
        if note is not None:
            note.cloud_revision = max(int(note.cloud_revision or 0), remote_revision)
            note.sync_status = SYNC_STATUS_CONFLICTED
            note.sync_conflict_id = f"note:{note.id}:remote:{remote_revision}"
            session.add(note)


def _mark_domain_entity_synced(session: Session, domain: str, entity_id: str, revision: int, object_key: str) -> None:
    if domain != "notes":
        return
    note = session.get(Note, _parse_int(entity_id))
    if note is None:
        return
    note.cloud_revision = revision
    note.local_revision = revision
    note.last_synced_revision = revision
    note.sync_status = SYNC_STATUS_SYNCED
    note.sync_conflict_id = ""
    note.cloud_object_key = object_key
    note.last_synced_at = utc_now()
    session.add(note)


def _domain_status(session: Session, domain: str) -> CloudSyncDomainStatus:
    items = session.exec(
        select(SyncItem)
        .where(SyncItem.provider == settings.storage_provider)
        .where(SyncItem.user_id == settings.storage_sync_user_id)
        .where(SyncItem.domain == domain)
    ).all()
    conflicts = session.exec(
        select(SyncConflict)
        .where(SyncConflict.provider == settings.storage_provider)
        .where(SyncConflict.user_id == settings.storage_sync_user_id)
        .where(SyncConflict.domain == domain)
        .where(SyncConflict.status == CONFLICT_STATUS_OPEN)
    ).all()
    return CloudSyncDomainStatus(
        domain=domain,
        manifest_key=domain_manifest_key(settings.storage_sync_user_id, domain),
        last_remote_revision=max([int(item.cloud_revision or 0) for item in items], default=0),
        dirty_count=sum(1 for item in items if item.status == SYNC_STATUS_DIRTY),
        conflict_count=len(conflicts),
        last_synced_at=max([item.last_synced_at for item in items if item.last_synced_at], default=None),
    )


def _record_device(session: Session, *, pulled: bool = False, pushed: bool = False) -> None:
    now = utc_now()
    device = session.exec(
        select(SyncDevice)
        .where(SyncDevice.provider == settings.storage_provider)
        .where(SyncDevice.user_id == settings.storage_sync_user_id)
        .where(SyncDevice.device_id == _device_id())
    ).first()
    if device is None:
        device = SyncDevice(
            provider=settings.storage_provider,
            user_id=settings.storage_sync_user_id,
            device_id=_device_id(),
            device_name=socket.gethostname(),
        )
    device.last_seen_at = now
    device.updated_at = now
    if pulled:
        device.last_pull_at = now
    if pushed:
        device.last_push_at = now
    session.add(device)


def _open_conflicts(session: Session) -> list[SyncConflict]:
    return session.exec(
        select(SyncConflict)
        .where(SyncConflict.provider == settings.storage_provider)
        .where(SyncConflict.user_id == settings.storage_sync_user_id)
        .where(SyncConflict.status == CONFLICT_STATUS_OPEN)
        .order_by(SyncConflict.created_at, SyncConflict.id)
    ).all()


def _conflict_read(conflict: SyncConflict) -> CloudSyncConflictRead:
    return CloudSyncConflictRead(
        id=int(conflict.id or 0),
        domain=conflict.domain,
        entity_id=conflict.entity_id,
        local_revision=conflict.local_revision,
        remote_revision=conflict.remote_revision,
        local_summary=conflict.local_summary,
        remote_summary=conflict.remote_summary,
        status=conflict.status,
        resolution=conflict.resolution,
        created_at=conflict.created_at,
        updated_at=conflict.updated_at,
    )


def _aggregate_result(status_text: str, results: list[CloudSyncDomainRunResult], *, message: str) -> CloudSyncRunResult:
    notes = next((result for result in results if result.domain == "notes"), None)
    return CloudSyncRunResult(
        status=status_text,
        uploaded_note_count=notes.uploaded_count if notes else 0,
        downloaded_note_count=notes.downloaded_count if notes else 0,
        skipped_note_count=sum(result.skipped_count for result in results),
        conflict_count=sum(result.conflict_count for result in results),
        message=message,
        domains=results,
    )


def _normalize_domains(domains: Iterable[str] | None) -> list[str]:
    if domains is None:
        return list(SYNC_DOMAINS)
    selected = []
    for domain in domains:
        normalized = domain.strip().lower()
        if normalized not in SYNC_DOMAINS:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"Unknown sync domain: {domain}")
        selected.append(normalized)
    return list(dict.fromkeys(selected))


def _domain_object_key(domain: str, entity_id: str) -> str:
    if domain == "notes":
        try:
            return note_object_key(settings.storage_sync_user_id, int(entity_id))
        except ValueError:
            pass
    return domain_object_key(settings.storage_sync_user_id, domain, entity_id)


def _model_payload(model: Any, *, exclude: set[str] | None = None) -> dict[str, Any]:
    excluded = exclude or set()
    result: dict[str, Any] = {}
    for name in model.__class__.model_fields:
        if name in excluded:
            continue
        value = getattr(model, name)
        result[name] = _json_value(value)
    return result


def _assign_fields(model: Any, data: dict[str, Any], *, exclude: set[str] | None = None) -> None:
    excluded = exclude or set()
    for name, field in model.__class__.model_fields.items():
        if name in excluded or name not in data:
            continue
        value = data.get(name)
        if value is not None and field.annotation in {datetime, datetime | None}:
            value = _parse_datetime(value)
        setattr(model, name, value)


def _json_value(value: Any) -> Any:
    if isinstance(value, datetime):
        return _to_iso(value)
    return value


def _payload_summary(payload: dict[str, Any]) -> str:
    if "title" in payload:
        return str(payload.get("title") or "")[:200]
    if "conversation" in payload:
        return str((payload.get("conversation") or {}).get("title") or "")[:200]
    if "space" in payload:
        return str((payload.get("space") or {}).get("name") or "")[:200]
    if "record" in payload:
        return str((payload.get("record") or {}).get("path") or (payload.get("record") or {}).get("content") or "")[:200]
    return ""


def _hash_payload(payload: dict[str, Any]) -> str:
    return hashlib.sha256(_json_bytes(payload)).hexdigest()


def _json_bytes(value: dict[str, Any]) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")


def _encode_tags(tags: list[str]) -> str:
    return ",".join(tag.strip() for tag in tags if tag.strip())


def _decode_tags(tags: str) -> list[str]:
    return [item for item in tags.split(",") if item] if tags else []


def _is_syncable_config(path: str) -> bool:
    normalized = path.strip()
    if not normalized.startswith(CONFIG_SYNC_PATH_PREFIXES):
        return False
    lowered = normalized.lower()
    return not any(keyword in lowered for keyword in CONFIG_SYNC_DENY_KEYWORDS)


def _parse_int(value: str | int | None) -> int:
    try:
        return int(value or 0)
    except (TypeError, ValueError):
        return 0


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


def _device_id() -> str:
    raw = f"{socket.gethostname()}:{settings.storage_sync_user_id}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]


def _backup_passphrase() -> str:
    return os.getenv("AIMEMO_BACKUP_PASSPHRASE", "").strip()


def _encrypt_backup_bytes(data: bytes, *, passphrase: str) -> bytes:
    try:
        from cryptography.fernet import Fernet  # type: ignore
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"code": "BACKUP_ENCRYPTION_UNAVAILABLE", "message": "cryptography is required for encrypted backups."},
        ) from exc
    key = base64.urlsafe_b64encode(hashlib.sha256(passphrase.encode("utf-8")).digest())
    token = Fernet(key).encrypt(data)
    return b"AIMEMO-BACKUP-V1\n" + token
