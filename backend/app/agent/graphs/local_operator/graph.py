from collections.abc import Callable
from contextlib import AbstractContextManager

from langgraph.graph import END, START, StateGraph
from sqlmodel import Session

from app.agent.graphs.local_operator.nodes import (
    ReadPlanner,
    build_finish_without_tool_node,
    build_observe_tool_result_node,
    build_plan_read_operation_node,
    build_run_read_tool_node,
    build_select_read_tool_node,
    build_summarize_findings_node,
    route_after_observe,
    route_after_plan,
)
from app.agent.graphs.local_operator.state import LocalOperatorState


SessionFactory = Callable[[], AbstractContextManager[Session]]


def build_local_operator_graph(
    *,
    session_factory: SessionFactory,
    planner: ReadPlanner | None = None,
):
    """构建 read-only Local Operator Graph。"""

    graph = StateGraph(LocalOperatorState)
    graph.add_node("plan_read_operation", build_plan_read_operation_node(planner))
    graph.add_node("select_read_tool", build_select_read_tool_node())
    graph.add_node("run_read_tool", build_run_read_tool_node(session_factory))
    graph.add_node("observe_tool_result", build_observe_tool_result_node())
    graph.add_node("finish_without_tool", build_finish_without_tool_node())
    graph.add_node("summarize_findings", build_summarize_findings_node())

    graph.add_edge(START, "plan_read_operation")
    graph.add_conditional_edges(
        "plan_read_operation",
        route_after_plan,
        ["select_read_tool", "finish_without_tool"],
    )
    graph.add_edge("select_read_tool", "run_read_tool")
    graph.add_edge("run_read_tool", "observe_tool_result")
    graph.add_conditional_edges(
        "observe_tool_result",
        route_after_observe,
        ["select_read_tool", "summarize_findings"],
    )
    graph.add_edge("finish_without_tool", END)
    graph.add_edge("summarize_findings", END)
    return graph


def run_local_operator_graph(
    *,
    session_factory: SessionFactory,
    conversation_id: int | None,
    turn_id: int | None,
    user_input: str,
    workspace_roots: list[str] | None = None,
    planner: ReadPlanner | None = None,
) -> LocalOperatorState:
    """执行 read-only Local Operator Graph。

    当前作为 Memory Chat Graph 的子图 worker 使用，所以先不单独配置 checkpointer；
    其结果会被外层 Memory Chat Graph checkpoint 捕获。
    """

    app = build_local_operator_graph(session_factory=session_factory, planner=planner).compile()
    return app.invoke(
        {
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "user_input": user_input,
            "workspace_roots": workspace_roots or ["."],
            "mode": "read",
        }
    )


def get_local_operator_graph_mermaid(*, session_factory: SessionFactory) -> str:
    """返回 Local Operator Graph 的 Mermaid 图。"""

    return build_local_operator_graph(session_factory=session_factory).compile().get_graph(xray=True).draw_mermaid()
