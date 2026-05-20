from typing import Literal, TypedDict


class LocalOperatorAction(TypedDict, total=False):
    """一次 read 工具调用计划。"""

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

    conversation_id/turn_id 用于审计关联；workspace_roots 是授权读取边界；
    tool_budget 防止模型或规则在目录里无限搜索。
    """

    conversation_id: int | None
    turn_id: int | None
    user_input: str
    workspace_roots: list[str]
    mode: Literal["read"]
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
