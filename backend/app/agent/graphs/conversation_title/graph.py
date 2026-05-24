from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path

from langgraph.graph import END, START, StateGraph
from sqlmodel import Session

from app.agent.checkpoints import get_sqlite_checkpointer
from app.agent.graphs.conversation_title.nodes import (
    TitleGenerator,
    build_generate_title_node,
    build_load_title_inputs_node,
    build_persist_title_node,
    generate_conversation_title,
    route_after_load_title,
)
from app.agent.graphs.conversation_title.state import ConversationTitleGraphState
from app.jobs.payloads import decode_payload
from app.models.job import Job


SessionFactory = Callable[[], AbstractContextManager[Session]]


def build_conversation_title_graph(
    *,
    session_factory: SessionFactory,
    title_generator: TitleGenerator = generate_conversation_title,
):
    """构建对话自动命名 graph。

    在用户发出第一条消息后异步执行：读首条 user 消息 → LLM → 写回 title。
    """

    graph = StateGraph(ConversationTitleGraphState)
    graph.add_node("load_title_inputs", build_load_title_inputs_node(session_factory))
    graph.add_node("generate_title", build_generate_title_node(title_generator))
    graph.add_node("persist_title", build_persist_title_node(session_factory))

    graph.add_edge(START, "load_title_inputs")
    graph.add_conditional_edges(
        "load_title_inputs",
        route_after_load_title,
        {
            "generate_title": "generate_title",
            "__end__": END,
        },
    )
    graph.add_edge("generate_title", "persist_title")
    graph.add_edge("persist_title", END)
    return graph


def run_conversation_title_graph(
    job: Job,
    *,
    session_factory: SessionFactory,
    checkpoint_path: str,
    title_generator: TitleGenerator = generate_conversation_title,
    interrupt_after: list[str] | None = None,
) -> None:
    payload = decode_payload(job.payload)
    conversation_id = int(payload["conversation_id"])
    thread_id = job.thread_id or f"job:{job.id}"
    checkpoint_file = Path(checkpoint_path)
    checkpoint_file.parent.mkdir(parents=True, exist_ok=True)

    with get_sqlite_checkpointer(str(checkpoint_file)) as checkpointer:
        app = build_conversation_title_graph(
            session_factory=session_factory,
            title_generator=title_generator,
        ).compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = app.get_state(config)
        graph_input = (
            None
            if snapshot.next
            else {"job_id": job.id or 0, "conversation_id": conversation_id}
        )
        app.invoke(graph_input, config, interrupt_after=interrupt_after)
