import asyncio
from io import BytesIO

from fastapi import UploadFile
from starlette.datastructures import Headers

from app.agent.graphs.memory_chat.nodes import build_lx_attachment_context_node
from app.schemas.conversation import ConversationCreate
from app.services.attachment_service import attach_attachments_to_message, create_chat_attachment
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


def _png_header(*, width: int, height: int) -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n"
        + b"\x00\x00\x00\rIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
    )
