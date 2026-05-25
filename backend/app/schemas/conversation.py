from datetime import datetime

from pydantic import BaseModel, Field


class ConversationCreate(BaseModel):
    """创建对话的请求体。

    title 可为空；为空时后端使用“新对话”。后续接入 AI 后，可以再由模型生成标题。
    """

    title: str = Field(default="", max_length=200)


class ConversationRead(BaseModel):
    """对话详情响应。"""

    id: int
    title: str
    status: str
    summary: str
    summary_message_id: int | None
    langgraph_thread_id: str
    created_at: datetime
    updated_at: datetime


class ConversationListItem(BaseModel):
    """对话列表项。当前与详情字段接近，后续可加入最后一条消息预览。"""

    id: int
    title: str
    status: str
    summary: str
    summary_message_id: int | None
    langgraph_thread_id: str
    created_at: datetime
    updated_at: datetime


class ChatMessageCreate(BaseModel):
    """追加消息的请求体。

    parent_id 用于显式指定消息树父节点；不传时后端默认接在当前会话最后一条消息后。
    checkpoint_id 在 MVP 中通常为空，接入 memory_chat_graph 后由 graph 执行结果写入。
    """

    role: str = Field(pattern="^(user|assistant|system)$")
    content: str = Field(min_length=1)
    parent_id: int | None = None
    checkpoint_id: str | None = Field(default=None, max_length=120)
    status: str = Field(default="completed", max_length=24)


class ChatMessageRead(BaseModel):
    """消息响应。"""

    id: int
    conversation_id: int
    role: str
    content: str
    parent_id: int | None
    checkpoint_id: str | None
    status: str
    token_count: int
    turn_id: int | None = None
    pending_interrupt: dict | None = None
    created_at: datetime
    updated_at: datetime
