from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path

from langgraph.graph import END, START, StateGraph
from sqlmodel import Session

from app.agent.checkpoints import get_sqlite_checkpointer
from app.agent.embeddings import embed_texts
from app.agent.graphs.knowledge_ingest.nodes import (
    EmbeddingGenerator,
    build_generate_embeddings_node,
    build_load_document_node,
    build_mark_failed_document,
    build_mark_ready_node,
    build_parse_and_chunk_node,
    build_persist_chunks_node,
    build_write_vector_index_node,
)
from app.agent.graphs.knowledge_ingest.state import KnowledgeIngestGraphState
from app.jobs.payloads import decode_payload
from app.models.job import Job


SessionFactory = Callable[[], AbstractContextManager[Session]]


def build_knowledge_ingest_graph(
    *,
    session_factory: SessionFactory,
    embedding_generator: EmbeddingGenerator = embed_texts,
):
    graph = StateGraph(KnowledgeIngestGraphState)
    graph.add_node("load_document", build_load_document_node(session_factory))
    graph.add_node("parse_and_chunk", build_parse_and_chunk_node())
    graph.add_node("persist_chunks", build_persist_chunks_node(session_factory))
    graph.add_node("generate_embeddings", build_generate_embeddings_node(embedding_generator))
    graph.add_node("write_vector_index", build_write_vector_index_node(session_factory))
    graph.add_node("mark_ready", build_mark_ready_node(session_factory))
    graph.add_edge(START, "load_document")
    graph.add_edge("load_document", "parse_and_chunk")
    graph.add_edge("parse_and_chunk", "persist_chunks")
    graph.add_edge("persist_chunks", "generate_embeddings")
    graph.add_edge("generate_embeddings", "write_vector_index")
    graph.add_edge("write_vector_index", "mark_ready")
    graph.add_edge("mark_ready", END)
    return graph


def run_knowledge_ingest_graph(
    job: Job,
    *,
    session_factory: SessionFactory,
    checkpoint_path: str,
    embedding_generator: EmbeddingGenerator = embed_texts,
    interrupt_after: list[str] | None = None,
) -> None:
    payload = decode_payload(job.payload)
    document_id = int(payload["document_id"])
    content_hash = str(payload.get("content_hash") or "")
    thread_id = job.thread_id or f"job:{job.id}"
    checkpoint_file = Path(checkpoint_path)
    checkpoint_file.parent.mkdir(parents=True, exist_ok=True)

    with get_sqlite_checkpointer(str(checkpoint_file)) as checkpointer:
        app = build_knowledge_ingest_graph(
            session_factory=session_factory,
            embedding_generator=embedding_generator,
        ).compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = app.get_state(config)
        graph_input = (
            None
            if snapshot.next
            else {"job_id": job.id or 0, "document_id": document_id, "content_hash": content_hash}
        )
        try:
            app.invoke(graph_input, config, interrupt_after=interrupt_after)
        except Exception as exc:
            build_mark_failed_document(session_factory)(job, str(exc))
            raise
