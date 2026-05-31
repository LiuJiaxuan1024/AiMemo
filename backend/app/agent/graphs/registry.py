from dataclasses import dataclass

from app.agent.checkpoints import get_sqlite_checkpointer
from app.agent.graphs.conversation_memory.graph import build_conversation_memory_graph
from app.agent.graphs.conversation_summary.graph import build_conversation_summary_graph
from app.agent.graphs.knowledge_ingest.graph import build_knowledge_ingest_graph
from app.agent.graphs.note_embedding.graph import build_note_embedding_graph
from app.agent.graphs.note_metadata.graph import build_note_metadata_graph
from app.core.config import settings
from app.core.database import session_scope
from app.jobs.models import GraphName
from app.models.job import Job


@dataclass(frozen=True)
class JobGraphView:
    mermaid: str
    next_nodes: list[str]


def get_job_graph_view(job: Job) -> JobGraphView:
    if job.graph_name == GraphName.NOTE_METADATA.value:
        return _get_note_metadata_graph_view(job)
    if job.graph_name == GraphName.NOTE_EMBEDDING.value:
        return _get_note_embedding_graph_view(job)
    if job.graph_name == GraphName.KNOWLEDGE_INGEST.value:
        return _get_knowledge_ingest_graph_view(job)
    if job.graph_name == GraphName.CONVERSATION_SUMMARY.value:
        return _get_conversation_summary_graph_view(job)
    if job.graph_name == GraphName.CONVERSATION_MEMORY.value:
        return _get_conversation_memory_graph_view(job)
    raise ValueError(f"Unsupported graph: {job.graph_name}")


def _get_note_metadata_graph_view(job: Job) -> JobGraphView:
    thread_id = job.thread_id or f"job:{job.id}"
    with get_sqlite_checkpointer(settings.langgraph_checkpoint_path) as checkpointer:
        app = build_note_metadata_graph(session_factory=session_scope).compile(
            checkpointer=checkpointer
        )
        snapshot = app.get_state({"configurable": {"thread_id": thread_id}})
        next_nodes = list(snapshot.next)
        mermaid = app.get_graph().draw_mermaid()

    return JobGraphView(
        mermaid=_highlight_nodes(mermaid, next_nodes),
        next_nodes=next_nodes,
    )


def _get_note_embedding_graph_view(job: Job) -> JobGraphView:
    thread_id = job.thread_id or f"job:{job.id}"
    with get_sqlite_checkpointer(settings.langgraph_checkpoint_path) as checkpointer:
        app = build_note_embedding_graph(session_factory=session_scope).compile(
            checkpointer=checkpointer
        )
        snapshot = app.get_state({"configurable": {"thread_id": thread_id}})
        next_nodes = list(snapshot.next)
        mermaid = app.get_graph().draw_mermaid()

    return JobGraphView(
        mermaid=_highlight_nodes(mermaid, next_nodes),
        next_nodes=next_nodes,
    )


def _get_knowledge_ingest_graph_view(job: Job) -> JobGraphView:
    thread_id = job.thread_id or f"job:{job.id}"
    with get_sqlite_checkpointer(settings.langgraph_checkpoint_path) as checkpointer:
        app = build_knowledge_ingest_graph(session_factory=session_scope).compile(
            checkpointer=checkpointer
        )
        snapshot = app.get_state({"configurable": {"thread_id": thread_id}})
        next_nodes = list(snapshot.next)
        mermaid = app.get_graph().draw_mermaid()

    return JobGraphView(
        mermaid=_highlight_nodes(mermaid, next_nodes),
        next_nodes=next_nodes,
    )


def _get_conversation_summary_graph_view(job: Job) -> JobGraphView:
    thread_id = job.thread_id or f"job:{job.id}"
    with get_sqlite_checkpointer(settings.langgraph_checkpoint_path) as checkpointer:
        app = build_conversation_summary_graph(session_factory=session_scope).compile(
            checkpointer=checkpointer
        )
        snapshot = app.get_state({"configurable": {"thread_id": thread_id}})
        next_nodes = list(snapshot.next)
        mermaid = app.get_graph().draw_mermaid()

    return JobGraphView(
        mermaid=_highlight_nodes(mermaid, next_nodes),
        next_nodes=next_nodes,
    )


def _get_conversation_memory_graph_view(job: Job) -> JobGraphView:
    thread_id = job.thread_id or f"job:{job.id}"
    with get_sqlite_checkpointer(settings.langgraph_checkpoint_path) as checkpointer:
        app = build_conversation_memory_graph(session_factory=session_scope).compile(
            checkpointer=checkpointer
        )
        snapshot = app.get_state({"configurable": {"thread_id": thread_id}})
        next_nodes = list(snapshot.next)
        mermaid = app.get_graph().draw_mermaid()

    return JobGraphView(
        mermaid=_highlight_nodes(mermaid, next_nodes),
        next_nodes=next_nodes,
    )


def _highlight_nodes(mermaid: str, node_names: list[str]) -> str:
    if not node_names:
        return mermaid

    lines = [
        mermaid.rstrip(),
        "classDef activeJobNode fill:#fff7ed,stroke:#f97316,stroke-width:3px,color:#9a3412;",
    ]
    for node_name in node_names:
        lines.append(f"class {node_name} activeJobNode;")
    return "\n".join(lines)
