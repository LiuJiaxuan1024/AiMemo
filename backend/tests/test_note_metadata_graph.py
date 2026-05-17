from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from app.ai.note_metadata import NoteMetadata
from app.agent.graphs.note_metadata.graph import (
    build_note_metadata_graph,
    run_note_metadata_graph,
)
from app.jobs.models import GraphName, JobStatus, JobType
from app.jobs.queue import complete_job, enqueue_job
from app.models.note import Note


def test_note_metadata_graph_resumes_after_generate_without_recalling_llm(
    session,
    session_factory,
    tmp_path: Path,
):
    note = Note(
        title="临时标题",
        title_source="fallback",
        content="今天研究 LangGraph checkpoint，要避免恢复时重复调用 LLM。",
        processing_status="pending",
    )
    session.add(note)
    session.commit()
    session.refresh(note)

    job = enqueue_job(
        session,
        job_type=JobType.NOTE_METADATA.value,
        graph_name=GraphName.NOTE_METADATA.value,
        payload={"note_id": note.id},
    )
    session.commit()
    session.refresh(job)

    calls: list[str] = []

    def fake_generator(content: str) -> NoteMetadata:
        calls.append(content)
        return NoteMetadata(title="Checkpoint 恢复测试", summary="验证恢复不重复调用模型。", tags=["LangGraph", "checkpoint"])

    checkpoint_path = tmp_path / "checkpoints.db"
    run_note_metadata_graph(
        job,
        session_factory=session_factory,
        checkpoint_path=str(checkpoint_path),
        metadata_generator=fake_generator,
        interrupt_after=["generate_metadata"],
    )

    with SqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        graph = build_note_metadata_graph(
            session_factory=session_factory,
            metadata_generator=fake_generator,
        ).compile(checkpointer=checkpointer)
        snapshot = graph.get_state({"configurable": {"thread_id": job.thread_id}})
        assert snapshot.next == ("write_metadata",)

    run_note_metadata_graph(
        job,
        session_factory=session_factory,
        checkpoint_path=str(checkpoint_path),
        metadata_generator=fake_generator,
    )

    session.refresh(note)
    assert len(calls) == 1
    assert note.title == "Checkpoint 恢复测试"
    assert note.summary == "验证恢复不重复调用模型。"
    assert note.tags == "LangGraph,checkpoint"
    assert note.processing_status == "completed"


def test_note_metadata_graph_preserves_user_title(session, session_factory, tmp_path: Path):
    note = Note(
        title="用户自己的标题",
        title_source="user",
        content="这条笔记应该保留用户标题。",
        processing_status="pending",
    )
    session.add(note)
    session.commit()
    session.refresh(note)

    job = enqueue_job(
        session,
        job_type=JobType.NOTE_METADATA.value,
        graph_name=GraphName.NOTE_METADATA.value,
        payload={"note_id": note.id},
    )
    session.commit()
    session.refresh(job)

    def fake_generator(_: str) -> NoteMetadata:
        return NoteMetadata(title="AI 生成标题", summary="摘要", tags=["标题"])

    run_note_metadata_graph(
        job,
        session_factory=session_factory,
        checkpoint_path=str(tmp_path / "checkpoints.db"),
        metadata_generator=fake_generator,
    )

    session.refresh(note)
    assert note.title == "用户自己的标题"
    assert note.summary == "摘要"
    assert note.processing_status == "completed"
