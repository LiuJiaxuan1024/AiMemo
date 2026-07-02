import json
from pathlib import Path

from sqlmodel import select

from app.core.config import settings
from app.models.chat_attachment import ChatAttachment
from app.models.conversation import Conversation
from app.models.note import Note
from app.schemas.note import NoteCreate, NoteUpdate
from app.services.cloud_key_service import cloud_object_key, domain_manifest_key, domain_object_key, manifest_key, note_object_key
from app.services.cloud_sync_service import (
    list_conflicts,
    pull_once,
    push_once,
    repair_conversation_attachment_storage_paths,
    resolve_conflict,
)
from app.services.note_service import create_note, create_note_category, delete_note, hard_delete_note, update_note
from app.storage.local_mock import LocalMockStorageProvider


def _configure_sync(monkeypatch, user_id: str = "u1") -> None:
    monkeypatch.setattr(settings, "storage_provider", "local_mock")
    monkeypatch.setattr(settings, "storage_sync_user_id", user_id)


def _put_remote_note(provider: LocalMockStorageProvider, user_id: str, note_id: int, payload: dict, *, deleted: bool) -> None:
    revision = int(payload["revision"])
    remote_manifest = {
        "schema_version": 1,
        "user_id": user_id,
        "global_revision": revision,
        "updated_at": payload["updated_at"],
        "device_id": "remote",
        "notes": {
            str(note_id): {
                "revision": revision,
                "content_hash": payload["content_hash"],
                "updated_at": payload["updated_at"],
                "deleted": deleted,
                "object_key": note_object_key(user_id, note_id),
            }
        },
    }
    provider.put_bytes(note_object_key(user_id, note_id), json.dumps(payload).encode("utf-8"), content_type="application/json")
    provider.put_bytes(manifest_key(user_id), json.dumps(remote_manifest).encode("utf-8"), content_type="application/json")


def _remote_tombstone_payload(note_id: int) -> dict:
    return {
        "schema_version": 1,
        "id": note_id,
        "title": "冲突笔记",
        "title_source": "user",
        "content_markdown": "初始",
        "content_blocks": "",
        "content_format": "markdown",
        "content_version": 1,
        "content_hash": "remote-delete-hash",
        "summary": "",
        "tags": [],
        "status": "deleted",
        "deleted_at": "2026-06-27T12:00:00Z",
        "created_at": "2026-06-13T01:00:00Z",
        "updated_at": "2026-06-27T12:00:00Z",
        "revision": 2,
        "object_key": note_object_key("u1", note_id),
        "attachments": [],
    }


def _conversation_payload_with_attachment(*, conversation_id: int, attachment_id: int, storage_path: str) -> dict:
    attachment_object_key = cloud_object_key("u1", "chat_attachments", conversation_id, attachment_id, "hash-photo.png")
    return {
        "schema_version": 1,
        "id": conversation_id,
        "domain": "conversations",
        "revision": 7,
        "object_key": domain_object_key("u1", "conversations", conversation_id),
        "conversation": {
            "id": conversation_id,
            "title": "带附件的对话",
            "status": "active",
            "summary": "",
            "summary_message_id": None,
            "active_task": "{}",
            "langgraph_thread_id": f"conversation:{conversation_id}",
            "created_at": "2026-06-29T01:00:00Z",
            "updated_at": "2026-06-29T01:10:00Z",
        },
        "messages": [],
        "attachments": [
            {
                "id": attachment_id,
                "conversation_id": conversation_id,
                "message_id": None,
                "kind": "image",
                "original_name": "photo.png",
                "storage_path": storage_path,
                "mime_type": "image/png",
                "size_bytes": 7,
                "width": None,
                "height": None,
                "sha256": "hash",
                "status": "ready",
                "retention_policy": "chat_only",
                "created_at": "2026-06-29T01:00:00Z",
                "updated_at": "2026-06-29T01:00:00Z",
                "cloud_object_key": attachment_object_key,
            }
        ],
        "attachment_derivatives": [],
        "updated_at": "2026-06-29T01:10:00Z",
    }


def _conversation_manifest(*, conversation_id: int, object_key: str, content_hash: str) -> dict:
    return {
        "schema_version": 1,
        "user_id": "u1",
        "domain": "conversations",
        "global_revision": 7,
        "updated_at": "2026-06-29T01:10:00Z",
        "device_id": "remote",
        "entities": {
            str(conversation_id): {
                "revision": 7,
                "content_hash": content_hash,
                "updated_at": "2026-06-29T01:10:00Z",
                "deleted": False,
                "object_key": object_key,
                "summary": "带附件的对话",
            }
        },
    }


def test_push_uploads_dirty_note_json_and_manifest(session, tmp_path, monkeypatch):
    _configure_sync(monkeypatch)
    provider = LocalMockStorageProvider(tmp_path)
    note = create_note(
        session,
        NoteCreate(
            title="云同步笔记",
            content_markdown="第一段",
            content_blocks='[{"id":"b1","type":"paragraph","text":"第一段"}]',
            content_format="blocknote",
            tags=["cloud"],
        ),
    )

    result = push_once(session, provider=provider)

    assert result.uploaded_note_count == 1
    manifest = json.loads(provider.get_bytes(manifest_key("u1")).decode("utf-8"))
    note_payload = json.loads(provider.get_bytes(note_object_key("u1", note.id)).decode("utf-8"))
    db_note = session.get(Note, note.id)
    assert manifest["global_revision"] == 1
    assert manifest["notes"][str(note.id)]["revision"] == 1
    assert note_payload["title"] == "云同步笔记"
    assert note_payload["content_blocks"] == '[{"id":"b1","type":"paragraph","text":"第一段"}]'
    assert db_note.sync_status == "synced"
    assert db_note.last_synced_revision == 1


def test_push_uploads_note_organization_fields(session, tmp_path, monkeypatch):
    _configure_sync(monkeypatch)
    provider = LocalMockStorageProvider(tmp_path)
    category = create_note_category(session, name="项目")
    note = create_note(session, NoteCreate(title="组织字段", content="内容"))
    update_note(
        session,
        note.id,
        NoteUpdate(category_id=category.id, tags=["project"], is_favorite=True, pinned=True),
    )

    push_once(session, provider=provider)

    note_payload = json.loads(provider.get_bytes(note_object_key("u1", note.id)).decode("utf-8"))
    assert note_payload["organization_schema_version"] == 1
    assert note_payload["category_id"] == category.id
    assert note_payload["category_name"] == "项目"
    assert note_payload["is_favorite"] is True
    assert note_payload["pinned_at"]
    assert note_payload["archived_at"] is None


def test_pull_downloads_changed_note_and_preserves_block_order(session, tmp_path, monkeypatch):
    _configure_sync(monkeypatch)
    provider = LocalMockStorageProvider(tmp_path)
    blocks = [
        {"id": "b1", "type": "paragraph", "order": 0, "text": "文字"},
        {"id": "b2", "type": "image", "order": 1, "object_key": "users/u1/notes/7/images/f1.png"},
        {"id": "b3", "type": "audio", "order": 2, "object_key": "users/u1/notes/7/attachments/f2.m4a"},
    ]
    note_payload = {
        "schema_version": 1,
        "id": 7,
        "title": "多模态笔记",
        "title_source": "user",
        "content_markdown": "文字\n\n![图](aimemo-object://f1)\n\n[audio](aimemo-object://f2)",
        "content_blocks": json.dumps(blocks, ensure_ascii=False),
        "content_format": "blocknote",
        "content_version": 1,
        "content_hash": "remote-hash",
        "summary": "",
        "tags": ["multi"],
        "status": "active",
        "deleted_at": None,
        "created_at": "2026-06-13T01:00:00Z",
        "updated_at": "2026-06-13T02:00:00Z",
        "revision": 3,
        "object_key": note_object_key("u1", 7),
        "attachments": [],
    }
    manifest = {
        "schema_version": 1,
        "user_id": "u1",
        "global_revision": 3,
        "updated_at": "2026-06-13T02:00:00Z",
        "device_id": "remote",
        "notes": {
            "7": {
                "revision": 3,
                "content_hash": "remote-hash",
                "updated_at": "2026-06-13T02:00:00Z",
                "deleted": False,
                "object_key": note_object_key("u1", 7),
            }
        },
    }
    provider.put_bytes(note_object_key("u1", 7), json.dumps(note_payload).encode("utf-8"), content_type="application/json")
    provider.put_bytes(manifest_key("u1"), json.dumps(manifest).encode("utf-8"), content_type="application/json")

    result = pull_once(session, provider=provider)

    notes = session.exec(select(Note)).all()
    assert result.downloaded_note_count == 1
    assert len(notes) == 1
    assert notes[0].id == 7
    assert notes[0].title == "多模态笔记"
    assert json.loads(notes[0].content_blocks) == blocks
    assert notes[0].category_id is None
    assert notes[0].is_favorite is False
    assert notes[0].pinned_at is None
    assert notes[0].cloud_revision == 3
    assert notes[0].sync_status == "synced"


def test_pull_conversation_attachment_remaps_foreign_absolute_storage_path(session, tmp_path, monkeypatch):
    _configure_sync(monkeypatch)
    local_uploads = tmp_path / "local-uploads"
    monkeypatch.setattr(settings, "attachments_storage_dir", str(local_uploads))
    provider = LocalMockStorageProvider(tmp_path / "cloud")
    conversation_id = 18
    attachment_id = 42
    remote_storage_path = "/home/wujie/project/AiMemo/backend/data/uploads/conversations/18/photo.png"
    object_key = cloud_object_key("u1", "chat_attachments", conversation_id, attachment_id, "hash-photo.png")
    payload = {
        "schema_version": 1,
        "id": conversation_id,
        "domain": "conversations",
        "revision": 2,
        "object_key": domain_object_key("u1", "conversations", conversation_id),
        "conversation": {
            "id": conversation_id,
            "title": "带附件的对话",
            "status": "active",
            "summary": "",
            "summary_message_id": None,
            "active_task": "{}",
            "langgraph_thread_id": f"conversation:{conversation_id}",
            "created_at": "2026-06-29T01:00:00Z",
            "updated_at": "2026-06-29T01:10:00Z",
        },
        "messages": [],
        "attachments": [
            {
                "id": attachment_id,
                "conversation_id": conversation_id,
                "message_id": None,
                "kind": "image",
                "original_name": "photo.png",
                "storage_path": remote_storage_path,
                "mime_type": "image/png",
                "size_bytes": 7,
                "width": None,
                "height": None,
                "sha256": "hash",
                "status": "ready",
                "retention_policy": "chat_only",
                "created_at": "2026-06-29T01:00:00Z",
                "updated_at": "2026-06-29T01:00:00Z",
                "cloud_object_key": object_key,
            }
        ],
        "attachment_derivatives": [],
        "updated_at": "2026-06-29T01:10:00Z",
    }
    manifest = {
        "schema_version": 1,
        "user_id": "u1",
        "domain": "conversations",
        "global_revision": 2,
        "updated_at": "2026-06-29T01:10:00Z",
        "device_id": "remote",
        "entities": {
            str(conversation_id): {
                "revision": 2,
                "content_hash": "remote-conversation-hash",
                "updated_at": "2026-06-29T01:10:00Z",
                "deleted": False,
                "object_key": domain_object_key("u1", "conversations", conversation_id),
            }
        },
    }
    provider.put_bytes(domain_object_key("u1", "conversations", conversation_id), json.dumps(payload).encode("utf-8"), content_type="application/json")
    provider.put_bytes(domain_manifest_key("u1", "conversations"), json.dumps(manifest).encode("utf-8"), content_type="application/json")
    provider.put_bytes(object_key, b"PNGDATA", content_type="image/png")

    result = pull_once(session, provider=provider, domains=["conversations"])
    attachment = session.get(ChatAttachment, attachment_id)

    assert result.downloaded_note_count == 0
    assert session.get(Conversation, conversation_id) is not None
    assert attachment is not None
    assert attachment.storage_path != remote_storage_path
    assert not Path(attachment.storage_path).is_absolute()
    assert attachment.storage_path == f"conversations/{conversation_id}/photo.png"
    assert (local_uploads / "conversations" / str(conversation_id) / "photo.png").read_bytes() == b"PNGDATA"


def test_push_conversation_attachment_payload_uses_portable_storage_path(session, tmp_path, monkeypatch):
    _configure_sync(monkeypatch)
    local_uploads = tmp_path / "local-uploads"
    monkeypatch.setattr(settings, "attachments_storage_dir", str(local_uploads))
    provider = LocalMockStorageProvider(tmp_path / "cloud")
    conversation = Conversation(id=18, title="带附件的对话")
    attachment_path = local_uploads / "conversations" / "18" / "hash-photo.png"
    attachment_path.parent.mkdir(parents=True)
    attachment_path.write_bytes(b"PNGDATA")
    attachment = ChatAttachment(
        id=42,
        conversation_id=18,
        kind="image",
        original_name="photo.png",
        storage_path=str(attachment_path.resolve()),
        mime_type="image/png",
        size_bytes=7,
        sha256="hash",
    )
    session.add(conversation)
    session.add(attachment)
    session.commit()

    result = push_once(session, provider=provider, domains=["conversations"])
    payload = json.loads(provider.get_bytes(domain_object_key("u1", "conversations", 18)).decode("utf-8"))
    uploaded_attachment = payload["attachments"][0]

    assert result.domains[0].uploaded_count == 1
    assert uploaded_attachment["storage_path"] == "conversations/18/hash-photo.png"
    assert not uploaded_attachment["storage_path"].startswith("/")
    assert provider.get_bytes(uploaded_attachment["cloud_object_key"]) == b"PNGDATA"


def test_repair_conversation_attachment_paths_dry_run_does_not_rewrite_cloud(session, tmp_path, monkeypatch):
    _configure_sync(monkeypatch)
    provider = LocalMockStorageProvider(tmp_path / "cloud")
    conversation_id = 18
    remote_storage_path = "/home/wujie/project/AiMemo/backend/data/uploads/conversations/18/photo.png"
    object_key = domain_object_key("u1", "conversations", conversation_id)
    payload = _conversation_payload_with_attachment(
        conversation_id=conversation_id,
        attachment_id=42,
        storage_path=remote_storage_path,
    )
    manifest = _conversation_manifest(conversation_id=conversation_id, object_key=object_key, content_hash="old-hash")
    provider.put_bytes(object_key, json.dumps(payload).encode("utf-8"), content_type="application/json")
    provider.put_bytes(domain_manifest_key("u1", "conversations"), json.dumps(manifest).encode("utf-8"), content_type="application/json")

    result = repair_conversation_attachment_storage_paths(provider=provider, dry_run=True)

    stored_payload = json.loads(provider.get_bytes(object_key).decode("utf-8"))
    stored_manifest = json.loads(provider.get_bytes(domain_manifest_key("u1", "conversations")).decode("utf-8"))
    assert result.dry_run is True
    assert result.scanned_count == 1
    assert result.repaired_count == 1
    assert result.repaired_attachment_count == 1
    assert stored_payload["attachments"][0]["storage_path"] == remote_storage_path
    assert stored_manifest["entities"][str(conversation_id)]["content_hash"] == "old-hash"


def test_repair_conversation_attachment_paths_rewrites_cloud_payload_and_manifest(session, tmp_path, monkeypatch):
    _configure_sync(monkeypatch)
    provider = LocalMockStorageProvider(tmp_path / "cloud")
    conversation_id = 18
    remote_storage_path = "/home/wujie/project/AiMemo/backend/data/uploads/conversations/18/photo.png"
    object_key = domain_object_key("u1", "conversations", conversation_id)
    payload = _conversation_payload_with_attachment(
        conversation_id=conversation_id,
        attachment_id=42,
        storage_path=remote_storage_path,
    )
    manifest = _conversation_manifest(conversation_id=conversation_id, object_key=object_key, content_hash="old-hash")
    provider.put_bytes(object_key, json.dumps(payload).encode("utf-8"), content_type="application/json")
    provider.put_bytes(domain_manifest_key("u1", "conversations"), json.dumps(manifest).encode("utf-8"), content_type="application/json")

    result = repair_conversation_attachment_storage_paths(provider=provider, dry_run=False)
    repaired_payload = json.loads(provider.get_bytes(object_key).decode("utf-8"))
    repaired_manifest = json.loads(provider.get_bytes(domain_manifest_key("u1", "conversations")).decode("utf-8"))

    assert result.dry_run is False
    assert result.scanned_count == 1
    assert result.repaired_count == 1
    assert result.repaired_attachment_count == 1
    assert repaired_payload["attachments"][0]["storage_path"] == "conversations/18/photo.png"
    assert repaired_manifest["global_revision"] == 8
    assert repaired_manifest["entities"][str(conversation_id)]["revision"] == 7
    assert repaired_manifest["entities"][str(conversation_id)]["content_hash"] != "old-hash"


def test_repair_conversation_attachment_paths_collapses_embedded_windows_absolute_path(session, tmp_path, monkeypatch):
    _configure_sync(monkeypatch)
    provider = LocalMockStorageProvider(tmp_path / "cloud")
    conversation_id = 18
    remote_storage_path = r"conversations/18/E:\Ai记\backend\data\uploads\conversations\18\image.png"
    object_key = domain_object_key("u1", "conversations", conversation_id)
    payload = _conversation_payload_with_attachment(
        conversation_id=conversation_id,
        attachment_id=42,
        storage_path=remote_storage_path,
    )
    manifest = _conversation_manifest(conversation_id=conversation_id, object_key=object_key, content_hash="old-hash")
    provider.put_bytes(object_key, json.dumps(payload).encode("utf-8"), content_type="application/json")
    provider.put_bytes(domain_manifest_key("u1", "conversations"), json.dumps(manifest).encode("utf-8"), content_type="application/json")

    result = repair_conversation_attachment_storage_paths(provider=provider, dry_run=False)
    repaired_payload = json.loads(provider.get_bytes(object_key).decode("utf-8"))

    assert result.repaired_count == 1
    assert result.repaired_attachment_count == 1
    assert repaired_payload["attachments"][0]["storage_path"] == "conversations/18/image.png"


def test_pull_marks_conflict_when_local_dirty_and_remote_changed(session, tmp_path, monkeypatch):
    _configure_sync(monkeypatch)
    provider = LocalMockStorageProvider(tmp_path)
    note = create_note(session, NoteCreate(content="本地初始"))
    push_once(session, provider=provider)
    update_note(session, note.id, NoteUpdate(content="本地修改"))

    remote_payload = {
        "schema_version": 1,
        "id": note.id,
        "title": "远端修改",
        "title_source": "user",
        "content_markdown": "远端修改",
        "content_blocks": "",
        "content_format": "markdown",
        "content_version": 2,
        "content_hash": "remote-hash",
        "summary": "",
        "tags": [],
        "status": "active",
        "deleted_at": None,
        "created_at": "2026-06-13T01:00:00Z",
        "updated_at": "2026-06-13T03:00:00Z",
        "revision": 2,
        "object_key": note_object_key("u1", note.id),
        "attachments": [],
    }
    remote_manifest = {
        "schema_version": 1,
        "user_id": "u1",
        "global_revision": 2,
        "updated_at": "2026-06-13T03:00:00Z",
        "device_id": "remote",
        "notes": {
            str(note.id): {
                "revision": 2,
                "content_hash": "remote-hash",
                "updated_at": "2026-06-13T03:00:00Z",
                "deleted": False,
                "object_key": note_object_key("u1", note.id),
            }
        },
    }
    provider.put_bytes(
        note_object_key("u1", note.id),
        json.dumps(remote_payload).encode("utf-8"),
        content_type="application/json",
    )
    provider.put_bytes(manifest_key("u1"), json.dumps(remote_manifest).encode("utf-8"), content_type="application/json")

    result = pull_once(session, provider=provider)
    conflicted = session.get(Note, note.id)

    assert result.conflict_count == 1
    assert conflicted.sync_status == "conflicted"
    assert conflicted.content == "本地修改"


def test_resolve_remote_changed_conflict_accepts_remote_payload(session, tmp_path, monkeypatch):
    _configure_sync(monkeypatch)
    provider = LocalMockStorageProvider(tmp_path)
    note = create_note(session, NoteCreate(title="冲突笔记", content="本地初始"))
    push_once(session, provider=provider)
    update_note(session, note.id, NoteUpdate(content="本地修改"))

    remote_payload = {
        "schema_version": 1,
        "id": note.id,
        "title": "远端标题",
        "title_source": "user",
        "content_markdown": "远端修改",
        "content_blocks": "",
        "content_format": "markdown",
        "content_version": 2,
        "content_hash": "remote-hash",
        "summary": "远端摘要",
        "tags": ["remote"],
        "status": "active",
        "deleted_at": None,
        "created_at": "2026-06-13T01:00:00Z",
        "updated_at": "2026-06-13T03:00:00Z",
        "revision": 2,
        "object_key": note_object_key("u1", note.id),
        "attachments": [],
    }
    remote_manifest = {
        "schema_version": 1,
        "user_id": "u1",
        "global_revision": 2,
        "updated_at": "2026-06-13T03:00:00Z",
        "device_id": "remote",
        "notes": {
            str(note.id): {
                "revision": 2,
                "content_hash": "remote-hash",
                "updated_at": "2026-06-13T03:00:00Z",
                "deleted": False,
                "object_key": note_object_key("u1", note.id),
            }
        },
    }
    provider.put_bytes(note_object_key("u1", note.id), json.dumps(remote_payload).encode("utf-8"), content_type="application/json")
    provider.put_bytes(manifest_key("u1"), json.dumps(remote_manifest).encode("utf-8"), content_type="application/json")
    pull_once(session, provider=provider)
    conflict = list_conflicts(session)[0]

    resolved = resolve_conflict(session, conflict.id, resolution="accept_remote", provider=provider)
    db_note = session.get(Note, note.id)

    assert resolved.status == "resolved"
    assert resolved.resolution == "accept_remote"
    assert db_note.title == "远端标题"
    assert db_note.content == "远端修改"
    assert db_note.sync_status == "synced"
    assert db_note.cloud_revision == 2
    assert list_conflicts(session) == []


def test_push_uploads_tombstone_for_deleted_note(session, tmp_path, monkeypatch):
    _configure_sync(monkeypatch)
    provider = LocalMockStorageProvider(tmp_path)
    note = create_note(session, NoteCreate(title="待删除", content="内容"))
    push_once(session, provider=provider)

    delete_note(session, note.id)
    result = push_once(session, provider=provider)

    manifest = json.loads(provider.get_bytes(manifest_key("u1")).decode("utf-8"))
    note_payload = json.loads(provider.get_bytes(note_object_key("u1", note.id)).decode("utf-8"))
    db_note = session.get(Note, note.id)
    assert result.uploaded_note_count == 1
    assert manifest["notes"][str(note.id)]["deleted"] is True
    assert manifest["notes"][str(note.id)]["revision"] == 2
    assert note_payload["status"] == "deleted"
    assert note_payload["deleted_at"]
    assert db_note.sync_status == "synced"
    assert db_note.status == "deleted"


def test_pull_applies_remote_tombstone_as_soft_delete(session, tmp_path, monkeypatch):
    _configure_sync(monkeypatch)
    provider = LocalMockStorageProvider(tmp_path)
    note = create_note(session, NoteCreate(title="本地存在", content="内容"))
    push_once(session, provider=provider)

    tombstone_payload = {
        "schema_version": 1,
        "id": note.id,
        "title": "本地存在",
        "title_source": "user",
        "content_markdown": "内容",
        "content_blocks": "",
        "content_format": "markdown",
        "content_version": 1,
        "content_hash": "remote-delete-hash",
        "summary": "",
        "tags": [],
        "status": "deleted",
        "deleted_at": "2026-06-27T12:00:00Z",
        "created_at": "2026-06-13T01:00:00Z",
        "updated_at": "2026-06-27T12:00:00Z",
        "revision": 2,
        "object_key": note_object_key("u1", note.id),
        "attachments": [],
    }
    remote_manifest = {
        "schema_version": 1,
        "user_id": "u1",
        "global_revision": 2,
        "updated_at": "2026-06-27T12:00:00Z",
        "device_id": "remote",
        "notes": {
            str(note.id): {
                "revision": 2,
                "content_hash": "remote-delete-hash",
                "updated_at": "2026-06-27T12:00:00Z",
                "deleted": True,
                "object_key": note_object_key("u1", note.id),
            }
        },
    }
    provider.put_bytes(
        note_object_key("u1", note.id),
        json.dumps(tombstone_payload).encode("utf-8"),
        content_type="application/json",
    )
    provider.put_bytes(manifest_key("u1"), json.dumps(remote_manifest).encode("utf-8"), content_type="application/json")

    result = pull_once(session, provider=provider)
    db_note = session.get(Note, note.id)

    assert result.downloaded_note_count == 1
    assert db_note.status == "deleted"
    assert db_note.deleted_at is not None
    assert db_note.cloud_revision == 2
    assert db_note.sync_status == "synced"


def test_pull_remote_tombstone_conflicts_with_local_dirty_note(session, tmp_path, monkeypatch):
    _configure_sync(monkeypatch)
    provider = LocalMockStorageProvider(tmp_path)
    note = create_note(session, NoteCreate(title="冲突笔记", content="初始"))
    push_once(session, provider=provider)
    update_note(session, note.id, NoteUpdate(content="本地后来修改"))

    tombstone_payload = {
        "schema_version": 1,
        "id": note.id,
        "title": "冲突笔记",
        "title_source": "user",
        "content_markdown": "初始",
        "content_blocks": "",
        "content_format": "markdown",
        "content_version": 1,
        "content_hash": "remote-delete-hash",
        "summary": "",
        "tags": [],
        "status": "deleted",
        "deleted_at": "2026-06-27T12:00:00Z",
        "created_at": "2026-06-13T01:00:00Z",
        "updated_at": "2026-06-27T12:00:00Z",
        "revision": 2,
        "object_key": note_object_key("u1", note.id),
        "attachments": [],
    }
    remote_manifest = {
        "schema_version": 1,
        "user_id": "u1",
        "global_revision": 2,
        "updated_at": "2026-06-27T12:00:00Z",
        "device_id": "remote",
        "notes": {
            str(note.id): {
                "revision": 2,
                "content_hash": "remote-delete-hash",
                "updated_at": "2026-06-27T12:00:00Z",
                "deleted": True,
                "object_key": note_object_key("u1", note.id),
            }
        },
    }
    provider.put_bytes(
        note_object_key("u1", note.id),
        json.dumps(tombstone_payload).encode("utf-8"),
        content_type="application/json",
    )
    provider.put_bytes(manifest_key("u1"), json.dumps(remote_manifest).encode("utf-8"), content_type="application/json")

    result = pull_once(session, provider=provider)
    db_note = session.get(Note, note.id)
    conflicts = list_conflicts(session)

    assert result.conflict_count == 1
    assert db_note.status == "active"
    assert db_note.content == "本地后来修改"
    assert db_note.sync_status == "conflicted"
    assert db_note.sync_conflict_id == f"note:{note.id}:remote:2"
    assert conflicts[0].conflict_type == "remote_deleted_local_modified"


def test_resolve_remote_tombstone_conflict_accepts_remote_delete(session, tmp_path, monkeypatch):
    _configure_sync(monkeypatch)
    provider = LocalMockStorageProvider(tmp_path)
    note = create_note(session, NoteCreate(title="冲突笔记", content="初始"))
    push_once(session, provider=provider)
    update_note(session, note.id, NoteUpdate(content="本地后来修改"))
    _put_remote_note(provider, "u1", note.id, _remote_tombstone_payload(note.id), deleted=True)
    pull_once(session, provider=provider)
    conflict = list_conflicts(session)[0]

    resolved = resolve_conflict(session, conflict.id, resolution="accept_remote", provider=provider)
    db_note = session.get(Note, note.id)

    assert resolved.status == "resolved"
    assert resolved.resolution == "accept_remote"
    assert db_note.status == "deleted"
    assert db_note.deleted_at is not None
    assert db_note.sync_status == "synced"
    assert db_note.sync_conflict_id == ""
    assert list_conflicts(session) == []


def test_resolve_remote_tombstone_conflict_keeps_local_dirty(session, tmp_path, monkeypatch):
    _configure_sync(monkeypatch)
    provider = LocalMockStorageProvider(tmp_path)
    note = create_note(session, NoteCreate(title="冲突笔记", content="初始"))
    push_once(session, provider=provider)
    update_note(session, note.id, NoteUpdate(content="本地后来修改"))
    _put_remote_note(provider, "u1", note.id, _remote_tombstone_payload(note.id), deleted=True)
    pull_once(session, provider=provider)
    conflict = list_conflicts(session)[0]

    resolved = resolve_conflict(session, conflict.id, resolution="keep_local", provider=provider)
    db_note = session.get(Note, note.id)

    assert resolved.status == "resolved"
    assert resolved.resolution == "keep_local"
    assert db_note.status == "active"
    assert db_note.content == "本地后来修改"
    assert db_note.sync_status == "dirty"
    assert db_note.sync_conflict_id == ""
    assert list_conflicts(session) == []


def test_resolve_remote_tombstone_conflict_keeps_both_as_local_copy(session, tmp_path, monkeypatch):
    _configure_sync(monkeypatch)
    provider = LocalMockStorageProvider(tmp_path)
    note = create_note(session, NoteCreate(title="冲突笔记", content="初始"))
    push_once(session, provider=provider)
    update_note(session, note.id, NoteUpdate(content="本地后来修改"))
    _put_remote_note(provider, "u1", note.id, _remote_tombstone_payload(note.id), deleted=True)
    pull_once(session, provider=provider)
    conflict = list_conflicts(session)[0]

    resolved = resolve_conflict(session, conflict.id, resolution="keep_both", provider=provider)
    original = session.get(Note, note.id)
    local_copy = session.exec(select(Note).where(Note.id != note.id)).first()

    assert resolved.status == "resolved"
    assert resolved.resolution == "keep_both"
    assert original.status == "deleted"
    assert original.sync_status == "synced"
    assert local_copy is not None
    assert local_copy.title == "冲突笔记（本机副本）"
    assert local_copy.content == "本地后来修改"
    assert local_copy.status == "active"
    assert local_copy.sync_status == "dirty"
    assert list_conflicts(session) == []


def test_hard_delete_keeps_hidden_tombstone_until_synced(session, tmp_path, monkeypatch):
    _configure_sync(monkeypatch)
    provider = LocalMockStorageProvider(tmp_path)
    note = create_note(session, NoteCreate(title="永久删除", content="敏感内容"))
    push_once(session, provider=provider)
    delete_note(session, note.id)

    hard_delete_note(session, note.id)
    result = push_once(session, provider=provider)

    db_note = session.get(Note, note.id)
    manifest = json.loads(provider.get_bytes(manifest_key("u1")).decode("utf-8"))
    note_payload = json.loads(provider.get_bytes(note_object_key("u1", note.id)).decode("utf-8"))
    assert result.uploaded_note_count == 1
    assert db_note.status == "purged"
    assert db_note.content == ""
    assert manifest["notes"][str(note.id)]["deleted"] is True
    assert note_payload["status"] == "purged"
