from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.note import utc_now


class AgentOperation(SQLModel, table=True):
    """本地操作智能体的审计记录。

    read/write/exec 都会写入这张表。第一阶段只落地 read，但字段从一开始
    为后续人工审批、失败恢复和前端调试面板预留。
    """

    # 详见 Note.__table_args__ 的注释。
    __table_args__ = {"sqlite_autoincrement": True}

    id: int | None = Field(default=None, primary_key=True)
    conversation_id: int | None = Field(default=None, index=True)
    turn_id: int | None = Field(default=None, index=True)
    operation_type: str = Field(index=True, max_length=24)
    status: str = Field(default="planned", index=True, max_length=24)
    tool_name: str = Field(default="", index=True, max_length=80)
    input_json: str = Field(default="{}")
    output_json: str = Field(default="{}")
    risk_level: str = Field(default="low", index=True, max_length=24)
    approval_required: bool = Field(default=False, index=True)
    approved_at: datetime | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now, index=True)
