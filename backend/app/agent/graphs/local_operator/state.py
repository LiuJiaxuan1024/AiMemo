from typing import Literal, TypedDict


class LocalOperatorAction(TypedDict, total=False):
    """一次本地工具调用计划。

    tool_name 是 LangChain @tool 的注册名；arguments 是传给该工具的结构化参数；
    reason 用于调试面板和审计排查，解释为什么本轮要调用这个工具。
    """

    tool_name: str
    arguments: dict
    reason: str


class LocalOperatorObservation(TypedDict, total=False):
    """一次工具调用后的观测结果。"""

    tool_name: str
    arguments: dict
    ok: bool
    data: dict
    error_code: str
    message: str
    blocked: bool


class LocalOperatorState(TypedDict, total=False):
    """Local Operator Graph 的状态。

    conversation_id/turn_id 用于审计关联；workspace_roots 是授权文件边界；
    tool_budget 防止 planner 或后续循环在目录里无限搜索/写入。
    """

    conversation_id: int | None
    turn_id: int | None
    user_input: str
    workspace_roots: list[str]
    mode: Literal["read", "write"]
    # 新字段：Local Operator 已经从 read-only 升级为通用工具调用循环。
    needs_tool: bool
    tool_intent: str
    # 旧字段短期保留，兼容历史测试、已有 checkpoint 和旧调试 payload。
    need_local_read: bool
    read_intent: str
    tool_budget: int
    planned_actions: list[LocalOperatorAction]
    next_action: LocalOperatorAction | None
    tool_calls: list[LocalOperatorAction]
    observations: list[LocalOperatorObservation]
    enough_evidence: bool
    final_answer: str
    error: str
