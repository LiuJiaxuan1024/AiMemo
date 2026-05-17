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
