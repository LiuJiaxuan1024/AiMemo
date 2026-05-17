from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from sqlmodel import select

from app.agent.graphs.note_embedding.graph import build_note_embedding_graph, run_note_embedding_graph
from app.core.config import settings
from app.jobs.models import GraphName, JobType
from app.jobs.queue import enqueue_job
from app.models.note import Note
from app.models.note_chunk import NoteChunk
from app.rag.vector_store import connect_vector_store, ensure_vector_store


def test_note_embedding_graph_writes_chunks_and_vectors(
    session,
    session_factory,
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(settings, "embedding_dimensions", 4)
    ensure_vector_store()

    note = Note(title="向量测试", content="这是第一段。\n\n这是第二段。", embedding_status="pending")
    session.add(note)
    session.commit()
    session.refresh(note)

    job = enqueue_job(
        session,
        job_type=JobType.NOTE_EMBEDDING.value,
        graph_name=GraphName.NOTE_EMBEDDING.value,
        payload={"note_id": note.id},
    )
    session.commit()
    session.refresh(job)

    def fake_embeddings(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0, float(index)] for index, _ in enumerate(texts)]

    run_note_embedding_graph(
        job,
        session_factory=session_factory,
        checkpoint_path=str(tmp_path / "checkpoints.db"),
        embedding_generator=fake_embeddings,
    )

    session.refresh(note)
    chunks = session.exec(select(NoteChunk).where(NoteChunk.note_id == note.id)).all()
    assert note.embedding_status == "completed"
    assert len(chunks) == 1
    assert chunks[0].embedding_status == "completed"

    with connect_vector_store() as connection:
        rows = connection.execute("select rowid from vec_note_chunks").fetchall()
    assert rows == [(chunks[0].id,)]


def test_note_embedding_graph_resumes_after_generate_without_reembedding(
    session,
    session_factory,
    tmp_path: Path,
    monkeypatch,
):
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(settings, "embedding_dimensions", 4)
    ensure_vector_store()

    note = Note(title="恢复测试", content="恢复时不要重复生成 embedding。", embedding_status="pending")
    session.add(note)
    session.commit()
    session.refresh(note)

    job = enqueue_job(
        session,
        job_type=JobType.NOTE_EMBEDDING.value,
        graph_name=GraphName.NOTE_EMBEDDING.value,
        payload={"note_id": note.id},
    )
    session.commit()
    session.refresh(job)

    calls: list[list[str]] = []

    def fake_embeddings(texts: list[str]) -> list[list[float]]:
        calls.append(texts)
        return [[0.0, 1.0, 0.0, 0.0] for _ in texts]

    checkpoint_path = tmp_path / "checkpoints.db"
    run_note_embedding_graph(
        job,
        session_factory=session_factory,
        checkpoint_path=str(checkpoint_path),
        embedding_generator=fake_embeddings,
        interrupt_after=["generate_embeddings"],
    )

    with SqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        graph = build_note_embedding_graph(
            session_factory=session_factory,
            embedding_generator=fake_embeddings,
        ).compile(checkpointer=checkpointer)
        snapshot = graph.get_state({"configurable": {"thread_id": job.thread_id}})
        assert snapshot.next == ("write_vector_index",)

    run_note_embedding_graph(
        job,
        session_factory=session_factory,
        checkpoint_path=str(checkpoint_path),
        embedding_generator=fake_embeddings,
    )

    session.refresh(note)
    assert len(calls) == 1
    assert note.embedding_status == "completed"
