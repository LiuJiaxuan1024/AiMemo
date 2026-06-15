import json

from sqlmodel import select

from app.core.config import settings
from app.models.chat_message import ChatMessage
from app.models.conversation import Conversation
from app.models.long_term_memory import LongTermMemory
from app.models.runtime_config import RuntimeConfig
from app.models.sync_metadata import SyncConflict, SyncItem
from app.rag.hashing import content_hash
from app.services.cloud_key_service import domain_manifest_key, domain_object_key
from app.services.cloud_sync_service import pull_once, push_once
from app.storage.local_mock import LocalMockStorageProvider


def _configure_sync(monkeypatch, user_id: str = "u1") -> None:
    monkeypatch.setattr(settings, "storage_provider", "local_mock")
    monkeypatch.setattr(settings, "storage_sync_user_id", user_id)


def test_conversation_domain_preserves_message_order_and_parent_tree(session, tmp_path, monkeypatch):
    _configure_sync(monkeypatch)
    provider = LocalMockStorageProvider(tmp_path)
    conversation = Conversation(title="同步对话", langgraph_thread_id="conversation:1")
    session.add(conversation)
    session.flush()
    first = ChatMessage(conversation_id=conversation.id or 0, role="user", content="第一问")
    session.add(first)
    session.flush()
    second = ChatMessage(conversation_id=conversation.id or 0, role="assistant", content="第一答", parent_id=first.id)
    session.add(second)
    session.commit()

    result = push_once(session, provider=provider, domains=["conversations"])

    assert result.domains[0].uploaded_count == 1
    manifest = json.loads(provider.get_bytes(domain_manifest_key("u1", "conversations")).decode("utf-8"))
    payload = json.loads(provider.get_bytes(domain_object_key("u1", "conversations", conversation.id)).decode("utf-8"))
    assert list(manifest["entities"]) == [str(conversation.id)]
    assert [message["content"] for message in payload["messages"]] == ["第一问", "第一答"]
    assert payload["messages"][1]["parent_id"] == first.id
    assert "checkpoint_id" not in payload["messages"][1]


def test_memory_and_config_domains_exclude_secret_like_config(session, tmp_path, monkeypatch):
    _configure_sync(monkeypatch)
    provider = LocalMockStorageProvider(tmp_path)
    memory = LongTermMemory(
        content="用户偏好绿色界面",
        content_hash=content_hash("用户偏好绿色界面"),
        memory_key="ui.color",
    )
    safe_config = RuntimeConfig(scope="user", path="elf.voice.mode", value_json='"text"')
    secret_config = RuntimeConfig(scope="user", path="models.agent_chat.api_key", value_json='"secret"')
    session.add(memory)
    session.add(safe_config)
    session.add(secret_config)
    session.commit()

    result = push_once(session, provider=provider, domains=["memories", "config"])

    assert {item.domain for item in result.domains} == {"memories", "config"}
    config_manifest = json.loads(provider.get_bytes(domain_manifest_key("u1", "config")).decode("utf-8"))
    assert len(config_manifest["entities"]) == 1
    config_key = next(iter(config_manifest["entities"].values()))["object_key"]
    config_payload = json.loads(provider.get_bytes(config_key).decode("utf-8"))
    assert config_payload["record"]["path"] == "elf.voice.mode"
    assert "api_key" not in json.dumps(config_payload, ensure_ascii=False)


def test_config_pull_matches_existing_row_by_scope_and_path(session, tmp_path, monkeypatch):
    _configure_sync(monkeypatch)
    provider = LocalMockStorageProvider(tmp_path)
    first_local = RuntimeConfig(scope="user", path="elf.voice.mode", value_json='"text"')
    target_config = RuntimeConfig(scope="user", path="elf.voice.default_profile_id", value_json='"1"')
    session.add(first_local)
    session.add(target_config)
    session.commit()

    object_key = domain_object_key("u1", "config", "runtime_config:1")
    remote_payload = {
        "schema_version": 1,
        "domain": "config",
        "id": "runtime_config:1",
        "revision": 2,
        "object_key": object_key,
        "kind": "runtime_config",
        "record": {
            "id": first_local.id,
            "scope": "user",
            "path": "elf.voice.default_profile_id",
            "value_json": '"3"',
            "created_at": "2026-06-13T01:00:00Z",
            "updated_at": "2026-06-13T02:00:00Z",
        },
        "updated_at": "2026-06-13T02:00:00Z",
    }
    remote_manifest = {
        "schema_version": 1,
        "user_id": "u1",
        "domain": "config",
        "global_revision": 2,
        "updated_at": "2026-06-13T02:00:00Z",
        "device_id": "remote",
        "entities": {
            "runtime_config:1": {
                "revision": 2,
                "content_hash": "remote-hash",
                "updated_at": "2026-06-13T02:00:00Z",
                "deleted": False,
                "object_key": object_key,
                "summary": "elf.voice.default_profile_id",
            }
        },
    }
    provider.put_bytes(object_key, json.dumps(remote_payload).encode("utf-8"), content_type="application/json")
    provider.put_bytes(domain_manifest_key("u1", "config"), json.dumps(remote_manifest).encode("utf-8"), content_type="application/json")

    result = pull_once(session, provider=provider, domains=["config"])

    session.refresh(first_local)
    session.refresh(target_config)
    assert result.domains[0].downloaded_count == 1
    assert first_local.path == "elf.voice.mode"
    assert target_config.value_json == '"3"'


def test_domain_conflict_is_recorded_when_local_dirty_and_remote_newer(session, tmp_path, monkeypatch):
    _configure_sync(monkeypatch)
    provider = LocalMockStorageProvider(tmp_path)
    conversation = Conversation(title="本地")
    session.add(conversation)
    session.commit()
    push_once(session, provider=provider, domains=["conversations"])
    conversation.title = "本地修改"
    session.add(conversation)
    item = session.exec(select(SyncItem)).first()
    item.status = "dirty"
    session.add(item)
    session.commit()

    object_key = domain_object_key("u1", "conversations", conversation.id)
    remote_payload = {
        "schema_version": 1,
        "domain": "conversations",
        "id": str(conversation.id),
        "revision": 2,
        "object_key": object_key,
        "conversation": {
            "id": conversation.id,
            "title": "远端修改",
            "status": "active",
            "summary": "",
            "summary_message_id": None,
            "active_task": "{}",
            "langgraph_thread_id": "",
            "created_at": "2026-06-13T01:00:00Z",
            "updated_at": "2026-06-13T02:00:00Z",
        },
        "messages": [],
        "attachments": [],
        "attachment_derivatives": [],
        "updated_at": "2026-06-13T02:00:00Z",
    }
    remote_manifest = {
        "schema_version": 1,
        "user_id": "u1",
        "domain": "conversations",
        "global_revision": 2,
        "updated_at": "2026-06-13T02:00:00Z",
        "device_id": "remote",
        "entities": {
            str(conversation.id): {
                "revision": 2,
                "content_hash": "remote-hash",
                "updated_at": "2026-06-13T02:00:00Z",
                "deleted": False,
                "object_key": object_key,
                "summary": "远端修改",
            }
        },
    }
    provider.put_bytes(object_key, json.dumps(remote_payload).encode("utf-8"), content_type="application/json")
    provider.put_bytes(domain_manifest_key("u1", "conversations"), json.dumps(remote_manifest).encode("utf-8"), content_type="application/json")

    result = pull_once(session, provider=provider, domains=["conversations"])

    assert result.conflict_count == 1
    conflict = session.exec(select(SyncConflict)).first()
    assert conflict is not None
    assert conflict.domain == "conversations"
