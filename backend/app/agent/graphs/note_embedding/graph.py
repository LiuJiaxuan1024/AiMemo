from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path

from langgraph.graph import END, START, StateGraph
from sqlmodel import Session

from app.agent.checkpoints import get_sqlite_checkpointer
from app.agent.embeddings import embed_texts
from app.agent.graphs.note_embedding.nodes import (
    EmbeddingGenerator,
    build_generate_embeddings_node,
    build_load_note_node,
    build_mark_completed_node,
    build_mark_failed_note,
    build_split_note_node,
    build_write_chunks_node,
    build_write_vector_index_node,
)
from app.agent.graphs.note_embedding.state import NoteEmbeddingGraphState
from app.jobs.payloads import decode_payload
from app.models.job import Job


SessionFactory = Callable[[], AbstractContextManager[Session]]


def build_note_embedding_graph(
    *,
    session_factory: SessionFactory,
    embedding_generator: EmbeddingGenerator = embed_texts,
):
    # 独立 embedding graph 只负责“笔记进入向量库”，不掺入标题/摘要/标签逻辑。
    graph = StateGraph(NoteEmbeddingGraphState)
    graph.add_node("load_note", build_load_note_node(session_factory))
    graph.add_node("split_note", build_split_note_node())
    graph.add_node("write_chunks", build_write_chunks_node(session_factory))
    graph.add_node("generate_embeddings", build_generate_embeddings_node(embedding_generator))
    graph.add_node("write_vector_index", build_write_vector_index_node(session_factory))
    graph.add_node("mark_embedding_completed", build_mark_completed_node(session_factory))
    graph.add_edge(START, "load_note")
    graph.add_edge("load_note", "split_note")
    graph.add_edge("split_note", "write_chunks")
    graph.add_edge("write_chunks", "generate_embeddings")
    graph.add_edge("generate_embeddings", "write_vector_index")
    graph.add_edge("write_vector_index", "mark_embedding_completed")
    graph.add_edge("mark_embedding_completed", END)
    return graph


def run_note_embedding_graph(
    job: Job,
    *,
    session_factory: SessionFactory,
    checkpoint_path: str,
    embedding_generator: EmbeddingGenerator = embed_texts,
    interrupt_after: list[str] | None = None,
) -> None:
    payload = decode_payload(job.payload)
    note_id = int(payload["note_id"])
    content_hash = str(payload.get("content_hash") or "")
    thread_id = job.thread_id or f"job:{job.id}"
    checkpoint_file = Path(checkpoint_path)
    checkpoint_file.parent.mkdir(parents=True, exist_ok=True)

    with get_sqlite_checkpointer(str(checkpoint_file)) as checkpointer:
        # 与 metadata graph 一样，执行时绑定具体 checkpointer，保证恢复读取同一 thread。
        app = build_note_embedding_graph(
            session_factory=session_factory,
            embedding_generator=embedding_generator,
        ).compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = app.get_state(config)
        graph_input = (
            None
            if snapshot.next
            else {"job_id": job.id or 0, "note_id": note_id, "content_hash": content_hash}
        )
        try:
            app.invoke(graph_input, config, interrupt_after=interrupt_after)
        except Exception as exc:
            build_mark_failed_note(session_factory)(job, str(exc))
            raise
