from datetime import datetime

from pydantic import BaseModel, Field


class MemoryRead(BaseModel):
    """长期记忆响应结构。"""

    id: int
    level: int
    category: str
    content: str
    summary: str
    importance: float
    confidence: float
    source_type: str
    source_id: int | None
    status: str
    content_hash: str
    created_at: datetime
    updated_at: datetime


class MemorySourceMessage(BaseModel):
    """长期记忆的来源消息。

    第一版只追踪 `source_type=chat_message`。如果未来记忆来自笔记、文件或手动录入，
    可以在不改变 `MemoryRead` 的前提下继续扩展这个来源对象。
    """

    id: int
    conversation_id: int
    conversation_title: str
    role: str
    content: str
    created_at: datetime


class MemoryDetail(MemoryRead):
    """长期记忆详情响应。

    列表接口保持轻量；详情接口额外返回来源消息，供前端排查“这条记忆从哪里来”。
    """

    source_message: MemorySourceMessage | None = None


class MemoryUpdate(BaseModel):
    """长期记忆更新请求。

    所有字段都可选，但 service 层会要求至少有一个字段被提供。
    """

    category: str | None = Field(default=None, max_length=40)
    content: str | None = None
    summary: str | None = None
    importance: float | None = None
    confidence: float | None = None
    status: str | None = Field(default=None, max_length=24)
