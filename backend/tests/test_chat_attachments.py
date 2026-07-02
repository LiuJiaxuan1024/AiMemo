import asyncio
from io import BytesIO
from pathlib import Path

from fastapi import UploadFile
from langchain_core.messages import AIMessage
from sqlmodel import select
from starlette.datastructures import Headers

from app.core import database
from app.core.config import settings
from app.models.chat_attachment import ChatAttachment
from app.agent.graphs.memory_chat.nodes import (
    INSPECT_IMAGE_ATTACHMENT_TOOL_NAME,
    _run_agent_tool_action,
    build_lx_attachment_context_node,
)
from app.schemas.conversation import ConversationCreate
from app.services.attachment_service import attach_attachments_to_message, create_chat_attachment, get_attachment_path_or_404
from app.services.conversation_service import append_message, create_conversation, list_messages
from app.schemas.conversation import ChatMessageCreate


def test_chat_attachment_upload_and_message_read(session, tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.attachments_storage_dir", str(tmp_path / "uploads"))
    conversation = create_conversation(session, ConversationCreate(title="附件"))
    upload = UploadFile(
        BytesIO(_png_header(width=3, height=2)),
        filename="screen.png",
        headers=Headers({"content-type": "image/png"}),
    )

    attachment = asyncio.run(
        create_chat_attachment(
            session,
            conversation_id=conversation.id,
            upload=upload,
        )
    )
    message = append_message(
        session,
        conversation.id,
        ChatMessageCreate(role="user", content="看图"),
    )
    attach_attachments_to_message(
        session,
        conversation_id=conversation.id,
        message_id=message.id,
        attachment_ids=[attachment.id],
    )
    session.commit()

    messages = list_messages(session, conversation.id)

    assert messages[0].attachments
    assert messages[0].attachments[0].kind == "image"
    assert messages[0].attachments[0].width == 3
    assert messages[0].attachments[0].height == 2
    assert messages[0].attachments[0].url.endswith(f"/attachments/{attachment.id}/content")


def test_chat_attachment_upload_stores_relative_path_and_resolves_file(session, tmp_path, monkeypatch):
    uploads_root = tmp_path / "uploads"
    monkeypatch.setattr("app.core.config.settings.attachments_storage_dir", str(uploads_root))
    conversation = create_conversation(session, ConversationCreate(title="相对附件"))
    data = _png_header(width=4, height=3)
    upload = UploadFile(
        BytesIO(data),
        filename="screen.png",
        headers=Headers({"content-type": "image/png"}),
    )

    attachment = asyncio.run(
        create_chat_attachment(
            session,
            conversation_id=conversation.id,
            upload=upload,
        )
    )
    stored = session.get(ChatAttachment, attachment.id)

    assert stored is not None
    assert not Path(stored.storage_path).is_absolute()
    assert stored.storage_path == f"conversations/{conversation.id}/{stored.sha256[:16]}-screen.png"
    resolved_path = get_attachment_path_or_404(
        session,
        conversation_id=conversation.id,
        attachment_id=attachment.id,
    )
    assert resolved_path == (uploads_root / stored.storage_path).resolve()
    assert resolved_path.read_bytes() == data


def test_legacy_absolute_chat_attachment_path_still_resolves(session, tmp_path, monkeypatch):
    uploads_root = tmp_path / "uploads"
    monkeypatch.setattr("app.core.config.settings.attachments_storage_dir", str(uploads_root))
    conversation = create_conversation(session, ConversationCreate(title="历史附件"))
    legacy_path = uploads_root / "conversations" / str(conversation.id) / "legacy.png"
    legacy_path.parent.mkdir(parents=True)
    legacy_path.write_bytes(b"legacy")
    attachment = ChatAttachment(
        conversation_id=conversation.id,
        kind="image",
        original_name="legacy.png",
        storage_path=str(legacy_path.resolve()),
        mime_type="image/png",
        size_bytes=6,
        sha256="legacyhash",
    )
    session.add(attachment)
    session.commit()
    session.refresh(attachment)

    resolved_path = get_attachment_path_or_404(
        session,
        conversation_id=conversation.id,
        attachment_id=int(attachment.id),
    )

    assert resolved_path == legacy_path.resolve()
    assert resolved_path.read_bytes() == b"legacy"


def test_migration_normalizes_chat_attachment_paths_under_current_root(session, tmp_path, monkeypatch):
    uploads_root = tmp_path / "uploads"
    monkeypatch.setattr(settings, "attachments_storage_dir", str(uploads_root))
    monkeypatch.setattr(database, "engine", session.get_bind())
    conversation = create_conversation(session, ConversationCreate(title="路径迁移"))
    conversation_id = int(conversation.id)
    legacy_path = uploads_root / "conversations" / str(conversation_id) / "legacy.png"
    external_path = tmp_path / "external" / "outside.png"
    legacy_path.parent.mkdir(parents=True)
    external_path.parent.mkdir(parents=True)
    legacy_path.write_bytes(b"legacy")
    external_path.write_bytes(b"outside")
    session.add(
        ChatAttachment(
            id=101,
            conversation_id=conversation_id,
            kind="image",
            original_name="legacy.png",
            storage_path=str(legacy_path.resolve()),
            mime_type="image/png",
            size_bytes=6,
            sha256="legacyhash",
        )
    )
    session.add(
        ChatAttachment(
            id=102,
            conversation_id=conversation_id,
            kind="image",
            original_name="outside.png",
            storage_path=str(external_path.resolve()),
            mime_type="image/png",
            size_bytes=7,
            sha256="outsidehash",
        )
    )
    session.commit()

    database._normalize_chat_attachment_storage_paths(ChatAttachment.__tablename__)

    session.expire_all()
    rows = session.exec(select(ChatAttachment.id, ChatAttachment.storage_path).order_by(ChatAttachment.id)).all()

    assert rows[0] == (101, f"conversations/{conversation_id}/legacy.png")
    assert rows[1] == (102, str(external_path.resolve()))


def test_lx_attachment_worker_builds_derivative_context(session_factory, tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.attachments_storage_dir", str(tmp_path / "uploads"))
    with session_factory() as session:
        conversation = create_conversation(session, ConversationCreate(title="附件上下文"))
        upload = UploadFile(
            BytesIO(_png_header(width=5, height=4)),
            filename="whiteboard.png",
            headers=Headers({"content-type": "image/png"}),
        )
        attachment = asyncio.run(
            create_chat_attachment(
                session,
                conversation_id=conversation.id,
                upload=upload,
            )
        )
        message = append_message(
            session,
            conversation.id,
            ChatMessageCreate(role="user", content="总结这张图"),
        )
        attach_attachments_to_message(
            session,
            conversation_id=conversation.id,
            message_id=message.id,
            attachment_ids=[attachment.id],
        )
        session.commit()
        conversation_id = conversation.id
        message_id = message.id

    update = build_lx_attachment_context_node(session_factory)(
        {
            "conversation_id": conversation_id,
            "user_message_id": message_id,
            "attachment_ids": [],
        }
    )

    layer = update["context_lx_attachment_layer"]
    assert layer["name"] == "附件派生上下文（Lx）"
    assert "attachment_id" in layer["content"]
    assert "whiteboard.png" in layer["content"]
    assert "image_dimensions: 5x4" in layer["content"]
    assert "回源重新解析" in layer["content"]


def test_lx_attachment_worker_auto_analyzes_current_turn_image(session_factory, tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.attachments_storage_dir", str(tmp_path / "uploads"))
    with session_factory() as session:
        conversation = create_conversation(session, ConversationCreate(title="自动看图"))
        upload = UploadFile(
            BytesIO(_png_header(width=8, height=7)),
            filename="chart.png",
            headers=Headers({"content-type": "image/png"}),
        )
        attachment = asyncio.run(
            create_chat_attachment(
                session,
                conversation_id=conversation.id,
                upload=upload,
            )
        )
        message = append_message(
            session,
            conversation.id,
            ChatMessageCreate(role="user", content="分析这张图片"),
        )
        attach_attachments_to_message(
            session,
            conversation_id=conversation.id,
            message_id=message.id,
            attachment_ids=[attachment.id],
        )
        session.commit()
        conversation_id = conversation.id
        message_id = message.id

    calls = 0

    class FakeVisionModel:
        def invoke(self, messages):
            nonlocal calls
            calls += 1
            return AIMessage(content="自动视觉分析：图片中有一张柱状图。")

    monkeypatch.setattr(
        "app.agent.graphs.memory_chat.nodes.get_vision_chat_model",
        lambda: FakeVisionModel(),
    )

    update = build_lx_attachment_context_node(session_factory)(
        {
            "conversation_id": conversation_id,
            "user_message_id": message_id,
            "attachment_ids": [],
        }
    )

    assert calls == 1
    assert "自动视觉分析：图片中有一张柱状图。" in update["context_lx_attachment_layer"]["content"]


def test_inspect_image_attachment_tool_uses_vision_model(session, session_factory, tmp_path, monkeypatch):
    monkeypatch.setattr("app.core.config.settings.attachments_storage_dir", str(tmp_path / "uploads"))
    conversation = create_conversation(session, ConversationCreate(title="图片解析"))
    upload = UploadFile(
        BytesIO(_png_header(width=7, height=6)),
        filename="diagram.png",
        headers=Headers({"content-type": "image/png"}),
    )
    attachment = asyncio.run(
        create_chat_attachment(
            session,
            conversation_id=conversation.id,
            upload=upload,
        )
    )

    captured: dict = {}

    class FakeVisionModel:
        def invoke(self, messages):
            content = messages[0].content
            captured["content"] = content
            return AIMessage(content="图片里是一张流程图，包含开始和结束节点。")

    monkeypatch.setattr(
        "app.agent.graphs.memory_chat.nodes.get_vision_chat_model",
        lambda: FakeVisionModel(),
    )

    update = _run_agent_tool_action(
        {"conversation_id": conversation.id, "tool_observations": [], "turn_messages": [], "thought_events": []},
        action={
            "tool_call_id": "inspect-image",
            "tool_name": INSPECT_IMAGE_ATTACHMENT_TOOL_NAME,
            "arguments": {
                "attachment_id": attachment.id,
                "instruction": "分析这张图片",
            },
        },
        session_factory=session_factory,
        allowed_tool_names={INSPECT_IMAGE_ATTACHMENT_TOOL_NAME},
    )

    observation = update["tool_observations"][0]
    assert observation["ok"] is True
    assert observation["data"]["analysis"] == "图片里是一张流程图，包含开始和结束节点。"
    assert observation["data"]["attachment_id"] == attachment.id
    assert captured["content"][1]["image_url"]["url"].startswith("data:image/png;base64,")


def _png_header(*, width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
    )
