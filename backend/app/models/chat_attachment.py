from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.note import utc_now


class ChatAttachment(SQLModel, table=True):
    """聊天消息附件。

    原始文件作为可回源证据保存；graph/prompt 默认消费派生文本。
    """

    __table_args__ = {"sqlite_autoincrement": True}

    id: int | None = Field(default=None, primary_key=True)
    conversation_id: int = Field(index=True)
    message_id: int | None = Field(default=None, index=True)
    kind: str = Field(default="file", index=True, max_length=24)
    original_name: str = Field(default="", max_length=255)
    storage_path: str = Field(default="", max_length=1000)
    mime_type: str = Field(default="", max_length=120)
    size_bytes: int = Field(default=0, index=True)
    width: int | None = Field(default=None)
    height: int | None = Field(default=None)
    sha256: str = Field(default="", index=True, max_length=64)
    status: str = Field(default="ready", index=True, max_length=24)
    retention_policy: str = Field(default="chat_only", max_length=40)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now, index=True)


class ChatAttachmentDerivative(SQLModel, table=True):
    """附件派生文本。

    OCR、caption、key facts 等都应该写到这里，并保留 source_hash 方便重算。
    """

    __table_args__ = {"sqlite_autoincrement": True}

    id: int | None = Field(default=None, primary_key=True)
    attachment_id: int = Field(index=True)
    kind: str = Field(default="metadata", index=True, max_length=40)
    content: str = Field(default="")
    model: str = Field(default="local-metadata", max_length=120)
    prompt_version: str = Field(default="v1", max_length=40)
    source_hash: str = Field(default="", index=True, max_length=64)
    status: str = Field(default="completed", index=True, max_length=24)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now, index=True)
