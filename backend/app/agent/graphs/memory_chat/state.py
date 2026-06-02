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


class MountedKnowledgeSpacePayload(TypedDict):
    """当前会话显式挂载的知识空间。"""

    space_id: int
    space_name: str
    space_icon: str | None
    ready_document_count: int
    document_count: int


class KnowledgeRetrievedChunkPayload(TypedDict):
    """会话挂载知库检索命中的 chunk 信息。"""

    chunk_id: int
    space_id: int
    space_name: str
    document_id: int
    document_title: str
    text: str
    score: float
    score_source: str
    heading_path: list[str]
    page_number: int | None
    source_uri: str | None
    original_filename: str | None
    retrieval_phase: str
    distance: float | None


class ElfBubblePayload(TypedDict):
    """外置精灵气泡回复片段。

    text 是该气泡展示的完整语义片段；emoji 用于驱动桌面精灵表情或动作。
    """

    text: str
    emoji: str


class AgentToolActionPayload(TypedDict, total=False):
    """ReAct agent 产生的一次本地工具调用。"""

    tool_call_id: str
    tool_name: str
    arguments: dict
    reason: str
    source_step_id: str
    operation_type: str
    risk_level: str
    requires_approval: bool
    status: Literal["READY", "EXECUTING", "COMPLETED", "FAILED", "CANCELLED", "SUPERSEDED"]


class AgentToolObservationPayload(TypedDict, total=False):
    """主对话 agent 循环中的一次工具观察结果。"""

    tool_call_id: str
    tool_name: str
    arguments: dict
    ok: bool
    data: dict
    error_code: str
    message: str
    blocked: bool


class AgentThoughtPayload(TypedDict, total=False):
    """给前端/桌面精灵展示的可审计过程摘要。"""

    id: str
    title: str
    summary: str
    status: Literal["running", "completed", "failed", "interrupted"]
    related_node: str
    related_tool_call_id: str | None
    # ReAct 循环里的步号；同一步的 thought/answer_delta/tool_invocation 在前端聚合到同一条 segment 中。
    step_index: int


class AgentTaskStepPayload(TypedDict, total=False):
    """本轮任务中的一个显式步骤。"""

    id: str
    description: str
    status: Literal["pending", "running", "completed", "failed", "skipped"]
    tool_name: str
    arguments: dict
    result_summary: str
    retry_count: int


class AgentTaskPayload(TypedDict, total=False):
    """主 ReAct 循环可见的任务模型。"""

    id: str
    goal: str
    status: Literal["planning", "running", "needs_user_input", "completed", "failed"]
    current_step_id: str
    steps: list[AgentTaskStepPayload]
    acceptance_criteria: list[str]
    assumptions: list[str]


class AgentWorldStatePayload(TypedDict, total=False):
    """工具执行后沉淀的世界状态摘要。"""

    known_paths: dict[str, dict]
    command_results: list[dict]
    background_tasks: list[dict]
    observations: list[dict]
    failures: list[dict]


class TurnMessagePayload(TypedDict, total=False):
    """单轮 graph 内部追加的消息流。

    跨轮历史由金字塔上下文重建；这个字段只记录本轮 user/agent/tool 的执行轨迹。
    assistant 消息如果包含 tool_calls，会保留结构化调用列表，确保下一轮 agent
    看到的是标准 AIMessage(tool_calls=...) + ToolMessage，而不是一段普通 JSON 文本。
    """

    role: Literal["user", "assistant", "tool", "system"]
    content: str
    name: str
    tool_call_id: str | None
    tool_calls: list[dict]


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
    mounted_knowledge_spaces: list[MountedKnowledgeSpacePayload]
    needs_knowledge_retrieval: bool
    knowledge_retrieval_query: str
    knowledge_retrieval_reason: str
    knowledge_retrieved_chunks: list[KnowledgeRetrievedChunkPayload]
    knowledge_retrieval_debug: dict
    context_conversation_window_layer: ContextLayerPayload
    context_l0_layer: ContextLayerPayload
    context_l1_layer: ContextLayerPayload
    context_l2_layer: ContextLayerPayload
    context_l3_layer: ContextLayerPayload
    context_l3_knowledge_layer: ContextLayerPayload
    context_l4_layer: ContextLayerPayload
    context_lx_attachment_layer: ContextLayerPayload
    attachment_ids: list[int]
    prompt_context: str
    # 本轮 graph 内部消息流。每轮 load_turn_state 会重新初始化，避免跨轮重复累加。
    turn_messages: list[TurnMessagePayload]
    tool_budget: int
    # 连续工具失败计数。tools 节点累加，成功一次会清零；agent 节点判断 >= 阈值后熔断。
    # 这是 ReAct 循环的兜底保险，防止模型在工具反复失败时仍然不停重试，耗尽 recursion_limit。
    consecutive_failed_tools: int
    # 当前 ReAct 步号；每次 agent 节点进入时 +1，关联到本步产生的 thought 与 tool_invocation。
    agent_step_index: int
    task: AgentTaskPayload
    world_state: AgentWorldStatePayload
    verification: dict
    replan_required: bool
    agent_decision: dict
    tool_observations: list[AgentToolObservationPayload]
    tool_observation_context: str
    thought_events: list[AgentThoughtPayload]
    # answer_mode 控制回答生成分支：普通 AiMemo 对话走 text，外置精灵走 elf_bubble。
    answer_mode: Literal["text", "elf_bubble"]
    assistant_answer: str
    elf_bubble_answer_parts: list[ElfBubblePayload]
    # 流式对话会在 graph 启动前先创建业务消息，刷新页面时也能看到本轮对话。
    # 非流式调用可以不传这两个字段，persist_messages 会按旧路径创建消息。
    user_message_id: int
    assistant_message_id: int
    parent_message_id: int
    graph_checkpoint_id: str | None
    error: str
