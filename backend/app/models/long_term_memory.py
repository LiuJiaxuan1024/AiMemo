from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.note import utc_now


class LongTermMemory(SQLModel, table=True):
    """用户长期记忆。

    第一版只服务 L4 核心长期记忆 prompt，不做向量化和复杂冲突合并。
    后续如果要支持长期记忆检索，可以为该表增加 embedding job。
    """

    # 详见 Note.__table_args__ 的注释。
    __table_args__ = {"sqlite_autoincrement": True}

    id: int | None = Field(default=None, primary_key=True)
    level: int = Field(default=4, index=True)
    category: str = Field(default="fact", index=True, max_length=40)
    memory_key: str = Field(default="", index=True, max_length=120)
    content: str
    summary: str = ""
    importance: float = Field(default=0.0, index=True)
    confidence: float = Field(default=0.0, index=True)
    reinforcement_count: int = Field(default=1, index=True)
    evidence_count: int = Field(default=1, index=True)
    evidence_source_ids: str = Field(default="[]")
    metadata_json: str = Field(default="{}")
    source_type: str = Field(default="chat_message", index=True, max_length=40)
    source_id: int | None = Field(default=None, index=True)
    status: str = Field(default="active", index=True, max_length=24)
    content_hash: str = Field(index=True, max_length=64)
    last_reinforced_at: datetime | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now, index=True)
