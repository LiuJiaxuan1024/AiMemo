from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from app.schemas.attachment import ChatAttachmentRead


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
    attachments: list[ChatAttachmentRead] = Field(default_factory=list)
    turn_id: int | None = None
    pending_interrupt: dict | None = None
    created_at: datetime
    updated_at: datetime


class ConversationExportRequest(BaseModel):
    """导出对话 HTML 的请求体。"""

    message_ids: list[int] = Field(default_factory=list)
    include_all: bool = False
    include_graphs: bool = False
    include_followups: bool = True


class ConversationMultiExportRequest(BaseModel):
    """批量导出多个对话的请求体。"""

    conversation_ids: list[int] = Field(default_factory=list, min_length=1)
    include_graphs: bool = False
    include_followups: bool = True


class ConversationExportConversation(BaseModel):
    """导出快照中的会话元数据。"""

    id: int
    title: str
    summary: str
    langgraph_thread_id: str
    exported_at: str


class ConversationExportAttachment(BaseModel):
    """导出快照中的附件元数据。

    data_uri 只在小型图片可内嵌时存在；大文件和非图片附件保持元数据展示。
    """

    id: int
    kind: str
    original_name: str
    mime_type: str
    size_bytes: int
    width: int | None = None
    height: int | None = None
    status: str
    url: str = ""
    data_uri: str | None = None


class ConversationExportFollowupTurn(BaseModel):
    """导出快照中的单轮片段追问。"""

    question: str
    answer: str = ""
    answer_html: str = ""
    assistant_message_id: int | None = None
    timestamp: str
    status: Literal["pending", "answered", "failed"]
    graph_id: str | None = None


class ConversationExportFollowupThread(BaseModel):
    """导出快照中的片段追问线程。"""

    segment_id: str
    original_text: str
    position: dict[str, int] | None = None
    status: Literal["pending", "answered", "failed"]
    turns: list[ConversationExportFollowupTurn] = Field(default_factory=list)


class ConversationExportMessage(BaseModel):
    """导出快照中的可见消息。"""

    id: int
    role: Literal["user", "assistant", "system"]
    content: str
    content_html: str
    created_at: str
    status: str
    token_count: int
    attachments: list[ConversationExportAttachment] = Field(default_factory=list)
    turn_id: int | None = None
    graph_id: str | None = None
    followup_threads: list[ConversationExportFollowupThread] = Field(default_factory=list)


class ConversationExportGraphSnapshot(BaseModel):
    """导出快照中的单轮 Graph 调试数据。"""

    turn_id: int
    conversation_id: int
    user_message_id: int | None
    assistant_message_id: int | None
    thread_id: str
    checkpoint_id: str | None
    status: str
    node_statuses: dict[str, str]
    mermaid: str
    subgraphs: dict[str, str] = Field(default_factory=dict)
    context_layers: list[Any] = Field(default_factory=list)
    retrieved_chunks: list[Any] = Field(default_factory=list)
    debug_payload: dict[str, Any] = Field(default_factory=dict)
    state_history: dict[str, Any] | None = None
    error: str = ""


class ConversationExportSnapshot(BaseModel):
    """在线聊天和离线导出共用的对话展示快照。"""

    schema_version: Literal[1] = 1
    conversation: ConversationExportConversation
    messages: list[ConversationExportMessage]
    graphs: dict[str, ConversationExportGraphSnapshot] = Field(default_factory=dict)


class ConversationMultiExportSnapshot(BaseModel):
    """多个对话打包到同一份静态 HTML 的导出快照。"""

    schema_version: Literal[1] = 1
    exported_at: str
    conversations: list[ConversationExportSnapshot]
