from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.note import utc_now


class LongTermMemory(SQLModel, table=True):
    """用户长期记忆。

    第一版只服务 L4 核心长期记忆 prompt，不做向量化和复杂冲突合并。
    后续如果要支持长期记忆检索，可以为该表增加 embedding job。
    """

    id: int | None = Field(default=None, primary_key=True)
    level: int = Field(default=4, index=True)
    category: str = Field(default="fact", index=True, max_length=40)
    content: str
    summary: str = ""
    importance: float = Field(default=0.0, index=True)
    confidence: float = Field(default=0.0, index=True)
    source_type: str = Field(default="chat_message", index=True, max_length=40)
    source_id: int | None = Field(default=None, index=True)
    status: str = Field(default="active", index=True, max_length=24)
    content_hash: str = Field(index=True, max_length=64)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now, index=True)
