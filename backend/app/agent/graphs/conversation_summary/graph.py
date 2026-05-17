from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path

from langgraph.graph import END, START, StateGraph
from sqlmodel import Session

from app.agent.checkpoints import get_sqlite_checkpointer
from app.agent.graphs.conversation_summary.nodes import (
    SummaryGenerator,
    build_load_summary_inputs_node,
    build_persist_summary_node,
    build_summarize_messages_node,
    generate_conversation_summary,
    route_after_load_summary,
)
from app.agent.graphs.conversation_summary.state import ConversationSummaryGraphState
from app.jobs.payloads import decode_payload
from app.models.job import Job


SessionFactory = Callable[[], AbstractContextManager[Session]]


def build_conversation_summary_graph(
    *,
    session_factory: SessionFactory,
    summary_generator: SummaryGenerator = generate_conversation_summary,
    trigger_tokens: int = 1500,
):
    """构建对话滚动摘要 graph。

    该 graph 独立于主聊天 graph，通过 job 后台运行，避免 L2 摘要更新阻塞用户对话。
    """

    graph = StateGraph(ConversationSummaryGraphState)
    graph.add_node(
        "load_summary_inputs",
        build_load_summary_inputs_node(
            session_factory,
            trigger_tokens=trigger_tokens,
        ),
    )
    graph.add_node("summarize_messages", build_summarize_messages_node(summary_generator))
    graph.add_node("persist_summary", build_persist_summary_node(session_factory))

    graph.add_edge(START, "load_summary_inputs")
    graph.add_conditional_edges(
        "load_summary_inputs",
        route_after_load_summary,
        {
            "summarize_messages": "summarize_messages",
            "__end__": END,
        },
    )
    graph.add_edge("summarize_messages", "persist_summary")
    graph.add_edge("persist_summary", END)
    return graph


def run_conversation_summary_graph(
    job: Job,
    *,
    session_factory: SessionFactory,
    checkpoint_path: str,
    summary_generator: SummaryGenerator = generate_conversation_summary,
    interrupt_after: list[str] | None = None,
    trigger_tokens: int = 1500,
) -> None:
    """执行 conversation_summary job。

    一个 job 对应一个 `job:{id}` thread。若进程在 graph 中途退出，
    再次领取同一个 job 时会从 checkpoint 的 next 节点继续执行。
    """

    payload = decode_payload(job.payload)
    conversation_id = int(payload["conversation_id"])
    thread_id = job.thread_id or f"job:{job.id}"
    checkpoint_file = Path(checkpoint_path)
    checkpoint_file.parent.mkdir(parents=True, exist_ok=True)

    with get_sqlite_checkpointer(str(checkpoint_file)) as checkpointer:
        app = build_conversation_summary_graph(
            session_factory=session_factory,
            summary_generator=summary_generator,
            trigger_tokens=trigger_tokens,
        ).compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = app.get_state(config)
        graph_input = (
            None
            if snapshot.next
            else {"job_id": job.id or 0, "conversation_id": conversation_id}
        )
        app.invoke(graph_input, config, interrupt_after=interrupt_after)
