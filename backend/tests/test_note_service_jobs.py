from sqlmodel import select

from app.jobs.models import GraphName, JobStatus, JobType
from app.models.job import Job
from app.schemas.note import NoteCreate
from app.services.note_service import create_note


def test_create_note_returns_immediately_with_pending_job(session):
    note = create_note(
        session,
        NoteCreate(content="今天想先把 Ai 记的后台任务队列做出来。"),
    )

    jobs = session.exec(select(Job).order_by(Job.id)).all()
    assert note.processing_status == "pending"
    assert note.embedding_status == "pending"
    assert note.summary == ""
    assert note.tags == []
    assert [job.type for job in jobs] == [
        JobType.NOTE_METADATA.value,
        JobType.NOTE_EMBEDDING.value,
    ]
    assert [job.status for job in jobs] == [JobStatus.PENDING.value, JobStatus.PENDING.value]
    assert [job.graph_name for job in jobs] == [
        GraphName.NOTE_METADATA.value,
        GraphName.NOTE_EMBEDDING.value,
    ]
    assert all(job.thread_id == f"job:{job.id}" for job in jobs)
