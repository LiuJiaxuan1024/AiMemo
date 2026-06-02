from datetime import datetime

from pydantic import BaseModel


class ChatAttachmentRead(BaseModel):
    id: int
    conversation_id: int
    message_id: int | None
    kind: str
    original_name: str
    mime_type: str
    size_bytes: int
    width: int | None
    height: int | None
    sha256: str
    status: str
    retention_policy: str
    url: str
    created_at: datetime
    updated_at: datetime


class ChatAttachmentDerivativeRead(BaseModel):
    id: int
    attachment_id: int
    kind: str
    content: str
    model: str
    prompt_version: str
    source_hash: str
    status: str
    created_at: datetime
    updated_at: datetime
