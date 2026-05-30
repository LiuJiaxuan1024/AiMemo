from datetime import datetime

from pydantic import BaseModel, Field

from app.schemas.conversation import ChatMessageRead
from app.schemas.search import NoteSearchResult


class ChatRequest(BaseModel):
    """发送给 memory_chat_graph 的用户输入。"""

    message: str = Field(min_length=1)
    parent_message_id: int | None = None


class ChatResumeRequest(BaseModel):
    """恢复被 interrupt 暂停的对话。"""

    request_id: str = Field(default="", max_length=120)
    request_ids: list[str] = Field(default_factory=list, max_length=20)
    selected_option_id: str = Field(default="", max_length=120)
    selected_option_ids: list[str] = Field(default_factory=list, max_length=20)
    answer: str = Field(default="", max_length=4000)
    answers: list[str] = Field(default_factory=list, max_length=20)
    question_answers: list[dict] = Field(default_factory=list, max_length=20)
    other_text: str = Field(default="", max_length=4000)


class ChatResponse(BaseModel):
    """一轮记忆对话的响应。"""

    conversation_id: int
    thread_id: str
    checkpoint_id: str | None
    needs_retrieval: bool
    needs_query_rewrite: bool
    retrieval_query: str
    retrieval_grade: str
    retrieval_grade_reason: str
    retrieval_reason: str
    user_message: ChatMessageRead
    assistant_message: ChatMessageRead
    retrieved_chunks: list[NoteSearchResult]


class ChatStreamEvent(BaseModel):
    """SSE 事件 payload 的统一结构。

    event 字段用于前端分流：node 表示 graph 节点进度，answer_delta 表示回答增量，
    done 表示本轮完成，error 表示失败。
    """

    event: str
    data: dict


class ChatTurnGraphRead(BaseModel):
    """单轮对话 graph 调试视图。"""

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
    context_layers: list[dict]
    retrieved_chunks: list[NoteSearchResult]
    debug_payload: dict
    error: str


class ChatCheckpointStateRead(BaseModel):
    """LangGraph 原生 checkpoint state history 中的一帧。"""

    checkpoint_id: str | None
    parent_checkpoint_id: str | None
    created_at: str | None
    next: list[str]
    tasks: list[dict]
    interrupts: list[dict]
    metadata: dict | None
    values: dict


class ChatTurnStateHistoryRead(BaseModel):
    """单轮对话关联 thread 的 checkpoint state history。"""

    turn_id: int
    conversation_id: int
    thread_id: str
    checkpoint_id: str | None
    states: list[ChatCheckpointStateRead]


class ChatActiveTurnRead(BaseModel):
    """当前会话里正在跑的一轮对话。

    用户切走、刷新、关掉再回来时，前端拿这个列表来恢复 "刚才那条 assistant
    消息还在生成" 的 UI 状态，然后订阅 /turns/{turn_id}/events/stream 接着拿增量。
    """

    turn_id: int
    conversation_id: int
    status: str
    node_statuses: dict[str, str]
    pending_interrupt: dict | None = None
    user_message: ChatMessageRead | None
    assistant_message: ChatMessageRead | None
    started_at: datetime
    updated_at: datetime


class ChatActiveTurnListRead(BaseModel):
    """会话的活跃 turn 列表响应。"""

    items: list[ChatActiveTurnRead]
