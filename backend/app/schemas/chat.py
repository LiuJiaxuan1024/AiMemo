from pydantic import BaseModel, Field

from app.schemas.conversation import ChatMessageRead
from app.schemas.search import NoteSearchResult


class ChatRequest(BaseModel):
    """发送给 memory_chat_graph 的用户输入。"""

    message: str = Field(min_length=1)


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
    context_layers: list[dict]
    retrieved_chunks: list[NoteSearchResult]
    debug_payload: dict
    error: str
