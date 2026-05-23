from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.note import utc_now


class Conversation(SQLModel, table=True):
    """一条对话线程。

    Conversation 是业务层的“聊天会话”，LangGraph 的 thread 只保存执行状态。
    用户可见的标题、摘要和消息列表都应以业务表为准。
    """

    id: int | None = Field(default=None, primary_key=True)
    title: str = Field(default="新对话", index=True, max_length=200)
    status: str = Field(default="active", index=True, max_length=24)
    # 滚动摘要用于长期对话压缩；MVP 先建字段，后续 summary graph 再写入。
    summary: str = ""
    # 表示 summary 已经覆盖到哪条消息，避免重复摘要或漏摘要。
    summary_message_id: int | None = Field(default=None, index=True)
    # JSON 字符串：保存跨 turn 的未完成本地执行任务。
    # LangGraph checkpoint 适合恢复同一轮执行现场；active_task 用于下一轮用户说
    # “继续/随便你/按你说的”时恢复上一轮未完成目标。
    active_task: str = Field(default="{}")
    # 约定为 conversation:{id}，用于绑定 LangGraph checkpoint thread。
    langgraph_thread_id: str = Field(default="", index=True, max_length=120)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now, index=True)
