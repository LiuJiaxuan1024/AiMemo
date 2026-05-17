from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path

from langgraph.graph import END, START, StateGraph
from sqlmodel import Session

from app.agent.checkpoints import get_sqlite_checkpointer
from app.agent.graphs.conversation_memory.nodes import (
    ConsolidationJudge,
    MemoryExtractor,
    build_consolidate_memories_node,
    build_extract_memories_node,
    build_load_memory_source_node,
    build_write_memories_node,
    extract_long_term_memories,
)
from app.agent.graphs.conversation_memory.state import ConversationMemoryGraphState
from app.jobs.payloads import decode_payload
from app.models.job import Job


SessionFactory = Callable[[], AbstractContextManager[Session]]


def build_conversation_memory_graph(
    *,
    session_factory: SessionFactory,
    memory_extractor: MemoryExtractor = extract_long_term_memories,
    consolidation_judge: ConsolidationJudge | None = None,
):
    """构建长期记忆抽取 graph。

    该 graph 通过 job 异步运行，不参与主聊天延迟路径。
    """

    graph = StateGraph(ConversationMemoryGraphState)
    graph.add_node("load_memory_source", build_load_memory_source_node(session_factory))
    graph.add_node("extract_memories", build_extract_memories_node(memory_extractor))
    graph.add_node(
        "consolidate_memories",
        build_consolidate_memories_node(session_factory, consolidation_judge),
    )
    graph.add_node("write_memories", build_write_memories_node(session_factory))
    graph.add_edge(START, "load_memory_source")
    graph.add_edge("load_memory_source", "extract_memories")
    graph.add_edge("extract_memories", "consolidate_memories")
    graph.add_edge("consolidate_memories", "write_memories")
    graph.add_edge("write_memories", END)
    return graph


def run_conversation_memory_graph(
    job: Job,
    *,
    session_factory: SessionFactory,
    checkpoint_path: str,
    memory_extractor: MemoryExtractor = extract_long_term_memories,
    consolidation_judge: ConsolidationJudge | None = None,
    interrupt_after: list[str] | None = None,
) -> None:
    """执行 conversation_memory job。"""

    payload = decode_payload(job.payload)
    thread_id = job.thread_id or f"job:{job.id}"
    checkpoint_file = Path(checkpoint_path)
    checkpoint_file.parent.mkdir(parents=True, exist_ok=True)

    with get_sqlite_checkpointer(str(checkpoint_file)) as checkpointer:
        app = build_conversation_memory_graph(
            session_factory=session_factory,
            memory_extractor=memory_extractor,
            consolidation_judge=consolidation_judge,
        ).compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = app.get_state(config)
        graph_input = (
            None
            if snapshot.next
            else {
                "job_id": job.id or 0,
                "conversation_id": int(payload["conversation_id"]),
                "user_message_id": int(payload["user_message_id"]),
                "assistant_message_id": int(payload["assistant_message_id"]),
            }
        )
        app.invoke(graph_input, config, interrupt_after=interrupt_after)
