from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.note import utc_now


class ChatMessage(SQLModel, table=True):
    """对话中的单条消息。

    这个表保存用户可见的消息历史。LangGraph checkpoint 可以重放执行状态，
    但真正展示、编辑、分支选择时仍需要依赖业务消息记录。
    """

    # 详见 Note.__table_args__ 的注释。
    __table_args__ = {"sqlite_autoincrement": True}

    id: int | None = Field(default=None, primary_key=True)
    conversation_id: int = Field(index=True)
    role: str = Field(index=True, max_length=24)
    content: str
    # 业务消息树的父节点。普通线性聊天中它指向上一条消息；
    # 未来编辑历史消息时，它可以帮助 UI 表达分支结构。
    parent_id: int | None = Field(default=None, index=True)
    # 当前消息对应的 LangGraph checkpoint。MVP 可为空，接入 graph 后写入。
    checkpoint_id: str | None = Field(default=None, index=True, max_length=120)
    status: str = Field(default="completed", index=True, max_length=24)
    token_count: int = Field(default=0, index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now, index=True)

