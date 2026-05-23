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


class AgentToolActionPayload(TypedDict, total=False):
    """主对话 agent 循环中的一次工具调用计划。"""

    tool_call_id: str
    tool_name: str
    arguments: dict
    reason: str
    source_step_id: str
    operation_type: str
    risk_level: str
    requires_approval: bool
    status: Literal["READY", "EXECUTING", "COMPLETED", "FAILED", "CANCELLED", "SUPERSEDED"]
    task_boundary: Literal["new_task", "continuation", "same_turn_followup"]


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


class TaskStepPayload(TypedDict, total=False):
    """Dynamic Execution Graph 中的一个动态执行步骤。"""

    id: str
    description: str
    kind: Literal["tool", "reasoning", "decision", "final"]
    tool_name: str | None
    arguments: dict
    dependencies: list[str]
    status: Literal[
        "PENDING",
        "READY",
        "EXECUTING",
        "COMPLETED",
        "FAILED",
        "BLOCKED",
        "WAITING_APPROVAL",
        "CANCELLED",
        "SUPERSEDED",
    ]
    retry_count: int
    attempt_count: int
    last_error: dict | None
    output_ref: str | None
    error: dict | None


class WorldStatePayload(TypedDict, total=False):
    """agent 对本轮任务执行世界的事实视图。"""

    cwd: str | None
    known_files: dict
    read_files: dict
    written_files: dict
    generated_outputs: dict
    observations: list[dict]
    failures: list[dict]
    # replanner 每次调用的压缩调试记录。用于定位“模型没规划好”还是“解析/合并失败”。
    replan_debug: list[dict]
    approvals: list[dict]


class WorldStatusPayload(TypedDict, total=False):
    """基于 WorldState 推导出的任务进展评估。

    WorldState 记录“发生了什么”，WorldStatus 判断“这些事实对当前目标意味着什么”。
    agent_think 只消费这个评估结果来决定继续、重规划或最终回答，避免工具 observation
    单独越权决定任务终止。
    """

    goal_satisfied: bool
    missing_requirements: list[str]
    requires_replan: bool
    replan_reason: str
    last_error: dict | None
    blocked_step_id: str | None
    recovery_hint: str
    recovery_path: str | None
    completed_steps: list[str]
    failed_steps: list[str]
    next_step_id: str | None
    contradictions: list[str]
    acceptance_summary: list[dict]


class GoalVerificationPayload(TypedDict, total=False):
    """当前 Task 的目标验收结果。

    该字段用于把“工具调用成功”和“用户目标完成”分开。工具 observation 是事实，
    goal_verification 是基于事实对用户目标的验收判断。
    """

    satisfied: bool
    reason: str
    missing_criteria: list[str]
    contradictions: list[str]
    evidence: list[dict]


class TaskPayload(TypedDict, total=False):
    """Dynamic Execution Graph 的任务对象。"""

    id: str
    goal: str
    source_user_message: str
    status: Literal[
        "PLANNING",
        "READY",
        "RUNNING",
        "WAITING_APPROVAL",
        "WAITING_USER_INPUT",
        "REPLANNING",
        "COMPLETED",
        "FAILED",
        "CANCELLED",
        "SUPERSEDED",
    ]
    plan_version: int
    current_step_id: str | None
    # pending_steps 是执行队列；completed_steps/failed_steps 是执行日志。
    # 旧的 steps 字段暂时保留为三者的镜像，兼容现有前端 graph 与测试。
    pending_steps: list[TaskStepPayload]
    completed_steps: list[TaskStepPayload]
    failed_steps: list[TaskStepPayload]
    steps: list[TaskStepPayload]
    world_state: WorldStatePayload
    execution_history: list[dict]
    replan_count: int


class TaskBoundaryPayload(TypedDict, total=False):
    """新一轮输入和上一轮 task 之间的边界判断结果。

    这个字段先存在 checkpoint state 中，后续落 `agent_tasks` 表时可以直接迁移为
    TaskSession 的边界事件。
    """

    type: Literal["fresh", "new_task", "continuation", "expired_stale_checkpoint"]
    reason: str
    previous_task_id: str | None
    active_task_id: str | None
    expired_task_id: str | None


class AgentThoughtPayload(TypedDict, total=False):
    """给前端/桌面精灵展示的可审计过程摘要。"""

    id: str
    title: str
    summary: str
    status: Literal["running", "completed", "failed", "interrupted"]
    related_node: str
    related_tool_call_id: str | None


class TurnMessagePayload(TypedDict, total=False):
    """单轮 graph 内部追加的消息流。

    跨轮历史由金字塔上下文重建；这个字段只记录本轮 user/agent/tool 的执行轨迹。
    """

    role: Literal["user", "assistant", "tool", "system"]
    content: str
    name: str
    tool_call_id: str | None


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
    context_conversation_window_layer: ContextLayerPayload
    context_l0_layer: ContextLayerPayload
    context_l1_layer: ContextLayerPayload
    context_l2_layer: ContextLayerPayload
    context_l3_layer: ContextLayerPayload
    context_l4_layer: ContextLayerPayload
    prompt_context: str
    # 本轮 graph 内部消息流。每轮 load_turn_state 会重新初始化，避免跨轮重复累加。
    turn_messages: list[TurnMessagePayload]
    tool_budget: int
    agent_decision: dict
    planned_tool_actions: list[AgentToolActionPayload]
    pending_tool_action: AgentToolActionPayload | None
    task_boundary: TaskBoundaryPayload
    expired_task: TaskPayload
    task: TaskPayload
    world_state: WorldStatePayload
    world_status: WorldStatusPayload
    goal_verification: GoalVerificationPayload
    tool_policy_result: dict
    tool_observations: list[AgentToolObservationPayload]
    tool_observation_context: str
    thought_events: list[AgentThoughtPayload]
    agent_loop_count: int
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
