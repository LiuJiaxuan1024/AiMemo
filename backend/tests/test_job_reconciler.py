from sqlmodel import select

from app.jobs.models import GraphName, JobStatus, JobType
from app.jobs.queue import enqueue_job
from app.jobs.reconciler import JobReconciler, reconcile_missing_jobs
from app.models.job import Job
from app.models.knowledge import KnowledgeDocument, KnowledgeSpace
from app.models.note import Note


def test_reconcile_enqueues_missing_note_jobs(session):
    note = Note(
        title="历史笔记",
        content="这是一条在 embedding job 出现前创建的历史笔记。",
        processing_status="pending",
        embedding_status="pending",
    )
    session.add(note)
    session.commit()

    result = reconcile_missing_jobs(session)

    jobs = session.exec(select(Job).order_by(Job.type)).all()
    assert result.metadata_jobs_created == 1
    assert result.embedding_jobs_created == 1
    assert {job.type for job in jobs} == {
        JobType.NOTE_METADATA.value,
        JobType.NOTE_EMBEDDING.value,
    }
    assert {job.graph_name for job in jobs} == {
        GraphName.NOTE_METADATA.value,
        GraphName.NOTE_EMBEDDING.value,
    }
    assert all(job.status == JobStatus.PENDING.value for job in jobs)
    assert all(job.thread_id == f"job:{job.id}" for job in jobs)


def test_reconcile_does_not_duplicate_active_jobs(session):
    note = Note(
        title="已有任务的笔记",
        content="这条笔记已经有活跃任务，不应该重复入队。",
        processing_status="pending",
        embedding_status="pending",
    )
    session.add(note)
    session.flush()
    assert note.id is not None
    enqueue_job(
        session,
        job_type=JobType.NOTE_METADATA.value,
        graph_name=GraphName.NOTE_METADATA.value,
        payload={"note_id": note.id},
        dedupe_key=f"{JobType.NOTE_METADATA.value}:note:{note.id}",
    )
    enqueue_job(
        session,
        job_type=JobType.NOTE_EMBEDDING.value,
        graph_name=GraphName.NOTE_EMBEDDING.value,
        payload={"note_id": note.id},
        dedupe_key=f"{JobType.NOTE_EMBEDDING.value}:note:{note.id}",
    )
    session.commit()

    result = reconcile_missing_jobs(session)

    jobs = session.exec(select(Job)).all()
    assert result.total_jobs_created == 0
    assert len(jobs) == 2


def test_reconcile_ignores_completed_note_statuses(session):
    note = Note(
        title="已完成笔记",
        content="状态已经完成，不需要补任务。",
        processing_status="completed",
        embedding_status="completed",
    )
    session.add(note)
    session.commit()

    result = reconcile_missing_jobs(session)

    jobs = session.exec(select(Job)).all()
    assert result.total_jobs_created == 0
    assert jobs == []


def test_job_reconciler_run_once_uses_same_rules(session_factory):
    with session_factory() as session:
        note = Note(
            title="周期检查笔记",
            content="周期检查器应该调用同一套 reconcile 规则。",
            processing_status="completed",
            embedding_status="pending",
        )
        session.add(note)
        session.commit()

    reconciler = JobReconciler(session_factory=session_factory, interval_seconds=60)
    result = reconciler.run_once()

    with session_factory() as session:
        jobs = session.exec(select(Job)).all()

    assert result.metadata_jobs_created == 0
    assert result.embedding_jobs_created == 1
    assert len(jobs) == 1
    assert jobs[0].type == JobType.NOTE_EMBEDDING.value


def test_reconcile_enqueues_missing_knowledge_ingest_jobs(session):
    space = KnowledgeSpace(name="知识空间")
    session.add(space)
    session.flush()
    document = KnowledgeDocument(
        space_id=space.id,
        title="文档",
        source_type="file",
        storage_path="files/1/1/original.md",
        content_hash="abc",
        parser="markdown",
        status="pending",
    )
    session.add(document)
    session.commit()

    result = reconcile_missing_jobs(session)

    jobs = session.exec(select(Job).where(Job.type == JobType.KNOWLEDGE_INGEST.value)).all()
    assert result.knowledge_ingest_jobs_created == 1
    assert len(jobs) == 1
    assert jobs[0].graph_name == GraphName.KNOWLEDGE_INGEST.value
