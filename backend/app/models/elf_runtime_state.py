from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.note import utc_now


class ElfRuntimeState(SQLModel, table=True):
    """桌面精灵的单例运行时状态。

    ChatTurn 记录 graph 执行；这里记录桌面精灵当前交互状态。刷新桌面精灵、
    打开只读状态面板或后端做 stale recovery 时，都以这张表作为事实来源。
    """

    id: int = Field(default=1, primary_key=True)
    status: str = Field(default="idle", index=True, max_length=32)
    conversation_id: int | None = Field(default=None, index=True)
    turn_id: int | None = Field(default=None, index=True)
    pending_interrupt: str = Field(default="{}")
    last_message: str = ""
    last_bubbles: str = Field(default="[]")
    last_error: str = ""
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now, index=True)
