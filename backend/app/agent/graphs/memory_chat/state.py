from typing import Literal, TypedDict


class ChatMessagePayload(TypedDict):
    """graph 内部使用的轻量消息结构，避免节点直接依赖 API schema。"""

    id: int
    role: str
    content: str
    token_count: int


class RetrievedChunkPayload(TypedDict):
    """RAG 检索命中的 chunk 信息。

    这里保留 distance/score，方便后续做 rerank、引用展示和调试。
    """

    note_id: int
    note_title: str
    chunk_id: int
    chunk_index: int
    content: str
    content_hash: str
    token_count: int
    distance: float
    score: float


class ElfBubblePayload(TypedDict):
    """外置精灵气泡回复片段。

    text 是该气泡展示的完整语义片段；emoji 用于驱动桌面精灵表情或动作。
    """

    text: str
    emoji: str


class ContextLayerPayload(TypedDict):
    """金字塔上下文单层 payload。

    worker 节点只写普通 dict，确保内容可以稳定进入 LangGraph checkpoint。
    """

    level: int
    name: str
    content: str
    budget_tokens: int | None
    used_tokens: int
    note: str


class MemoryChatGraphState(TypedDict, total=False):
    """memory_chat_graph 的共享状态。

    当前是 MVP 字段集：先支持近期上下文、是否检索、检索结果、回答和落库消息 ID。
    后续做摘要、长期记忆、query rewrite 时，可以在这里继续扩展。
    """

    conversation_id: int
    user_message: str
    langgraph_thread_id: str
    recent_messages: list[ChatMessagePayload]
    conversation_summary: str
    intent: Literal["direct", "rag"]
    needs_retrieval: bool
    needs_query_rewrite: bool
    retrieval_query: str
    plan_confidence: float
    retrieval_reason: str
    retrieved_chunks: list[RetrievedChunkPayload]
    retrieval_grade: Literal["good", "weak", "poor", "none"]
    retrieval_grade_reason: str
    # L3 内部调试信息：记录 planner/retriever/grade/layer 的耗时，方便定位慢点。
    retrieval_debug: dict
    context_l0_layer: ContextLayerPayload
    context_l1_layer: ContextLayerPayload
    context_l2_layer: ContextLayerPayload
    context_l3_layer: ContextLayerPayload
    context_l4_layer: ContextLayerPayload
    # 本轮本地 read-only 工具结果。为空表示没有触发 Local Operator。
    local_operator_context: str
    prompt_context: str
    # answer_mode 控制回答生成分支：普通 AiMemo 对话走 text，外置精灵走 elf_bubble。
    answer_mode: Literal["text", "elf_bubble"]
    assistant_answer: str
    elf_bubble_answer_parts: list[ElfBubblePayload]
    # 流式对话会在 graph 启动前先创建业务消息，刷新页面时也能看到本轮对话。
    # 非流式调用可以不传这两个字段，persist_messages 会按旧路径创建消息。
    user_message_id: int
    assistant_message_id: int
    graph_checkpoint_id: str | None
    error: str
