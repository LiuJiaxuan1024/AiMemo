from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.note import utc_now


class ChatTurn(SQLModel, table=True):
    """一次用户提问到 AI 回复的 graph 执行记录。

    ChatMessage 保存用户可见的消息；ChatTurn 保存这一轮背后的执行现场。
    这样前端点击某条 assistant 消息时，可以反查本轮 Memory Chat Graph 的
    节点状态、上下文金字塔和检索证据。
    """

    id: int | None = Field(default=None, primary_key=True)
    conversation_id: int = Field(index=True)
    user_message_id: int | None = Field(default=None, index=True)
    assistant_message_id: int | None = Field(default=None, index=True)
    thread_id: str = Field(index=True, max_length=120)
    checkpoint_id: str | None = Field(default=None, index=True, max_length=120)
    status: str = Field(default="running", index=True, max_length=24)
    # JSON 字符串：{"node_name": "pending|running|succeeded|failed|skipped"}
    node_statuses: str = Field(default="{}")
    # JSON 字符串：L0-L4 上下文层，供调试面板查看模型本轮吃到什么。
    context_layers: str = Field(default="[]")
    # JSON 字符串：L3 检索命中的 chunk 列表，供排查 RAG 质量。
    retrieved_chunks: str = Field(default="[]")
    # JSON 字符串：性能埋点和调试信息，例如节点耗时、首 token 时间、L3 子步骤耗时。
    debug_payload: str = Field(default="{}")
    error: str = ""
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now, index=True)
