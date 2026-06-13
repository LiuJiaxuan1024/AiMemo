import json

from sqlmodel import select

from app.core.config import settings
from app.models.note import Note
from app.schemas.note import NoteCreate, NoteUpdate
from app.services.cloud_key_service import manifest_key, note_object_key
from app.services.cloud_sync_service import pull_once, push_once
from app.services.note_service import create_note, update_note
from app.storage.local_mock import LocalMockStorageProvider


def _configure_sync(monkeypatch, user_id: str = "u1") -> None:
    monkeypatch.setattr(settings, "storage_provider", "local_mock")
    monkeypatch.setattr(settings, "storage_sync_user_id", user_id)


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
    assert notes[0].cloud_revision == 3
    assert notes[0].sync_status == "synced"


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
