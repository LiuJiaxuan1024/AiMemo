from __future__ import annotations

import hashlib
import os
import re
import struct
from pathlib import Path

from fastapi import HTTPException, UploadFile, status
from sqlmodel import Session, select

from app.core.config import settings
from app.models.chat_attachment import ChatAttachment, ChatAttachmentDerivative
from app.models.conversation import Conversation
from app.models.note import utc_now
from app.schemas.attachment import ChatAttachmentRead


_IMAGE_MIME_TYPES = {"image/jpeg", "image/png", "image/gif", "image/webp"}


async def create_chat_attachment(
    session: Session,
    *,
    conversation_id: int,
    upload: UploadFile,
) -> ChatAttachmentRead:
    conversation = session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Conversation not found")

    original_name = _safe_original_name(upload.filename or "attachment")
    mime_type = upload.content_type or _guess_mime_type(original_name)
    kind = "image" if mime_type in _IMAGE_MIME_TYPES else "file"
    upload.file.seek(0)
    data = upload.file.read()
    size_bytes = len(data)
    _validate_upload(kind=kind, mime_type=mime_type, size_bytes=size_bytes)

    sha256 = hashlib.sha256(data).hexdigest()
    width, height = _read_image_dimensions(data, mime_type) if kind == "image" else (None, None)
    storage_path = _write_attachment_file(
        conversation_id=conversation_id,
        sha256=sha256,
        original_name=original_name,
        data=data,
    )
    attachment = ChatAttachment(
        conversation_id=conversation_id,
        kind=kind,
        original_name=original_name,
        storage_path=str(storage_path),
        mime_type=mime_type,
        size_bytes=size_bytes,
        width=width,
        height=height,
        sha256=sha256,
        retention_policy=settings.attachments_chat_image_default_policy,
    )
    session.add(attachment)
    session.flush()
    if attachment.id is None:
        raise RuntimeError("Attachment id was not generated.")
    if settings.attachments_auto_extract:
        session.add(_build_metadata_derivative(attachment))
    session.commit()
    session.refresh(attachment)
    return to_chat_attachment_read(attachment)


def attach_attachments_to_message(
    session: Session,
    *,
    conversation_id: int,
    message_id: int,
    attachment_ids: list[int],
) -> list[ChatAttachment]:
    if not attachment_ids:
        return []
    unique_ids = list(dict.fromkeys(int(item) for item in attachment_ids if int(item) > 0))
    if not unique_ids:
        return []
    attachments = session.exec(
        select(ChatAttachment)
        .where(ChatAttachment.conversation_id == conversation_id)
        .where(ChatAttachment.id.in_(unique_ids))
    ).all()
    found_ids = {item.id for item in attachments}
    missing_ids = [item for item in unique_ids if item not in found_ids]
    if missing_ids:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Attachment not found: {missing_ids[0]}",
        )
    for attachment in attachments:
        if attachment.message_id is not None and attachment.message_id != message_id:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"Attachment already belongs to message {attachment.message_id}",
            )
        attachment.message_id = message_id
        attachment.updated_at = utc_now()
        session.add(attachment)
    return attachments


def list_message_attachments(
    session: Session,
    *,
    conversation_id: int,
    message_ids: list[int],
) -> dict[int, list[ChatAttachmentRead]]:
    if not message_ids:
        return {}
    attachments = session.exec(
        select(ChatAttachment)
        .where(ChatAttachment.conversation_id == conversation_id)
        .where(ChatAttachment.message_id.in_(message_ids))
        .order_by(ChatAttachment.created_at, ChatAttachment.id)
    ).all()
    grouped: dict[int, list[ChatAttachmentRead]] = {}
    for attachment in attachments:
        if attachment.message_id is None:
            continue
        grouped.setdefault(attachment.message_id, []).append(to_chat_attachment_read(attachment))
    return grouped


def get_attachment_or_404(
    session: Session,
    *,
    conversation_id: int,
    attachment_id: int,
) -> ChatAttachment:
    attachment = session.get(ChatAttachment, attachment_id)
    if attachment is None or attachment.conversation_id != conversation_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment not found")
    return attachment


def get_attachment_path_or_404(
    session: Session,
    *,
    conversation_id: int,
    attachment_id: int,
) -> Path:
    attachment = get_attachment_or_404(
        session,
        conversation_id=conversation_id,
        attachment_id=attachment_id,
    )
    path = Path(attachment.storage_path)
    if not path.exists() or not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Attachment file not found")
    return path


def load_attachment_context_for_message(
    session: Session,
    *,
    conversation_id: int,
    message_id: int | None,
    attachment_ids: list[int] | None = None,
) -> list[tuple[ChatAttachment, list[ChatAttachmentDerivative]]]:
    query = select(ChatAttachment).where(ChatAttachment.conversation_id == conversation_id)
    ids = [int(item) for item in attachment_ids or [] if int(item) > 0]
    if ids:
        query = query.where(ChatAttachment.id.in_(list(dict.fromkeys(ids))))
    elif message_id:
        query = query.where(ChatAttachment.message_id == message_id)
    else:
        return []
    attachments = session.exec(query.order_by(ChatAttachment.created_at, ChatAttachment.id)).all()
    if not attachments:
        return []
    attachment_ids_loaded = [item.id for item in attachments if item.id is not None]
    derivatives = session.exec(
        select(ChatAttachmentDerivative)
        .where(ChatAttachmentDerivative.attachment_id.in_(attachment_ids_loaded))
        .order_by(ChatAttachmentDerivative.created_at, ChatAttachmentDerivative.id)
    ).all()
    grouped: dict[int, list[ChatAttachmentDerivative]] = {}
    for derivative in derivatives:
        grouped.setdefault(derivative.attachment_id, []).append(derivative)
    return [(attachment, grouped.get(attachment.id or 0, [])) for attachment in attachments]


def delete_attachments_for_messages(
    session: Session,
    *,
    conversation_id: int,
    message_ids: list[int],
) -> None:
    if not message_ids:
        return
    attachments = session.exec(
        select(ChatAttachment)
        .where(ChatAttachment.conversation_id == conversation_id)
        .where(ChatAttachment.message_id.in_(message_ids))
    ).all()
    _delete_attachment_records(session, attachments)


def delete_attachments_for_conversation(session: Session, *, conversation_id: int) -> None:
    attachments = session.exec(
        select(ChatAttachment).where(ChatAttachment.conversation_id == conversation_id)
    ).all()
    _delete_attachment_records(session, attachments)


def to_chat_attachment_read(attachment: ChatAttachment) -> ChatAttachmentRead:
    attachment_id = int(attachment.id or 0)
    return ChatAttachmentRead(
        id=attachment_id,
        conversation_id=attachment.conversation_id,
        message_id=attachment.message_id,
        kind=attachment.kind,
        original_name=attachment.original_name,
        mime_type=attachment.mime_type,
        size_bytes=attachment.size_bytes,
        width=attachment.width,
        height=attachment.height,
        sha256=attachment.sha256,
        status=attachment.status,
        retention_policy=attachment.retention_policy,
        url=f"/api/conversations/{attachment.conversation_id}/attachments/{attachment_id}/content",
        created_at=attachment.created_at,
        updated_at=attachment.updated_at,
    )


def _validate_upload(*, kind: str, mime_type: str, size_bytes: int) -> None:
    if size_bytes <= 0:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Attachment is empty")
    if kind == "image":
        if mime_type not in set(settings.attachments_allowed_image_mime_types):
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=f"Unsupported image type: {mime_type}")
        max_bytes = settings.attachments_image_max_mb * 1024 * 1024
    else:
        max_bytes = settings.attachments_file_max_mb * 1024 * 1024
    if size_bytes > max_bytes:
        max_mb = settings.attachments_image_max_mb if kind == "image" else settings.attachments_file_max_mb
        raise HTTPException(status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE, detail=f"Attachment exceeds {max_mb} MB")


def _write_attachment_file(
    *,
    conversation_id: int,
    sha256: str,
    original_name: str,
    data: bytes,
) -> Path:
    root = Path(settings.attachments_storage_dir).expanduser()
    target_dir = root / "conversations" / str(conversation_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    safe_name = _safe_storage_name(original_name)
    target = target_dir / f"{sha256[:16]}-{safe_name}"
    if target.exists():
        return target.resolve()
    tmp = target.with_suffix(target.suffix + ".part")
    tmp.write_bytes(data)
    os.replace(tmp, target)
    return target.resolve()


def _build_metadata_derivative(attachment: ChatAttachment) -> ChatAttachmentDerivative:
    size_label = _format_size(attachment.size_bytes)
    lines = [
        f"附件：{attachment.original_name}",
        f"- attachment_id: {attachment.id}",
        f"- kind: {attachment.kind}",
        f"- mime_type: {attachment.mime_type}",
        f"- size: {size_label}",
        f"- storage_path: {attachment.storage_path}",
        f"- source_hash: {attachment.sha256}",
    ]
    if attachment.width and attachment.height:
        lines.append(f"- image_dimensions: {attachment.width}x{attachment.height}")
    lines.append("- 提取状态：当前为基础元数据；如果派生信息不足，应回源读取原始附件重新解析。")
    return ChatAttachmentDerivative(
        attachment_id=int(attachment.id or 0),
        kind="metadata",
        content="\n".join(lines),
        source_hash=attachment.sha256,
    )


def _delete_attachment_records(session: Session, attachments: list[ChatAttachment]) -> None:
    for attachment in attachments:
        derivatives = session.exec(
            select(ChatAttachmentDerivative).where(ChatAttachmentDerivative.attachment_id == attachment.id)
        ).all()
        for derivative in derivatives:
            session.delete(derivative)
        path = Path(attachment.storage_path)
        try:
            if path.exists() and path.is_file():
                path.unlink()
        except OSError:
            pass
        session.delete(attachment)


def _safe_original_name(name: str) -> str:
    cleaned = Path(name).name.strip() or "attachment"
    return cleaned[:255]


def _safe_storage_name(name: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9._-]+", "_", _safe_original_name(name))
    return cleaned.strip("._") or "attachment"


def _guess_mime_type(name: str) -> str:
    suffix = Path(name).suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".gif": "image/gif",
        ".webp": "image/webp",
        ".txt": "text/plain",
        ".md": "text/markdown",
        ".pdf": "application/pdf",
    }.get(suffix, "application/octet-stream")


def _read_image_dimensions(data: bytes, mime_type: str) -> tuple[int | None, int | None]:
    try:
        if mime_type == "image/png" and data.startswith(b"\x89PNG\r\n\x1a\n") and len(data) >= 24:
            width, height = struct.unpack(">II", data[16:24])
            return int(width), int(height)
        if mime_type == "image/gif" and data[:6] in {b"GIF87a", b"GIF89a"} and len(data) >= 10:
            width, height = struct.unpack("<HH", data[6:10])
            return int(width), int(height)
        if mime_type == "image/jpeg":
            return _read_jpeg_dimensions(data)
        if mime_type == "image/webp":
            return _read_webp_dimensions(data)
    except Exception:
        return None, None
    return None, None


def _read_jpeg_dimensions(data: bytes) -> tuple[int | None, int | None]:
    if not data.startswith(b"\xff\xd8"):
        return None, None
    index = 2
    while index + 9 < len(data):
        if data[index] != 0xFF:
            index += 1
            continue
        marker = data[index + 1]
        index += 2
        if marker in {0xD8, 0xD9}:
            continue
        if index + 2 > len(data):
            return None, None
        segment_length = int.from_bytes(data[index:index + 2], "big")
        if segment_length < 2 or index + segment_length > len(data):
            return None, None
        if 0xC0 <= marker <= 0xC3 or 0xC5 <= marker <= 0xC7 or 0xC9 <= marker <= 0xCB or 0xCD <= marker <= 0xCF:
            height = int.from_bytes(data[index + 3:index + 5], "big")
            width = int.from_bytes(data[index + 5:index + 7], "big")
            return width, height
        index += segment_length
    return None, None


def _read_webp_dimensions(data: bytes) -> tuple[int | None, int | None]:
    if len(data) < 30 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None, None
    chunk = data[12:16]
    if chunk == b"VP8X" and len(data) >= 30:
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
        return width, height
    if chunk == b"VP8 " and len(data) >= 30:
        width = int.from_bytes(data[26:28], "little") & 0x3FFF
        height = int.from_bytes(data[28:30], "little") & 0x3FFF
        return width, height
    return None, None


def _format_size(size_bytes: int) -> str:
    if size_bytes >= 1024 * 1024:
        return f"{size_bytes / 1024 / 1024:.1f} MB"
    if size_bytes >= 1024:
        return f"{size_bytes / 1024:.1f} KB"
    return f"{size_bytes} B"
