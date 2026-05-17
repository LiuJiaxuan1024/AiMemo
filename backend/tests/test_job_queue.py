from datetime import timedelta

from app.jobs.models import GraphName, JobStatus, JobType
from app.jobs.queue import claim_next_job, enqueue_job, recover_stale_running_jobs, utc_now


def test_enqueue_job_dedupes_active_jobs(session):
    first = enqueue_job(
        session,
        job_type=JobType.NOTE_METADATA.value,
        graph_name=GraphName.NOTE_METADATA.value,
        payload={"note_id": 1},
        dedupe_key="note_metadata:note:1",
    )
    second = enqueue_job(
        session,
        job_type=JobType.NOTE_METADATA.value,
        graph_name=GraphName.NOTE_METADATA.value,
        payload={"note_id": 1},
        dedupe_key="note_metadata:note:1",
    )

    assert first.id == second.id
    assert first.thread_id == f"job:{first.id}"


def test_claim_next_job_marks_running(session):
    enqueue_job(
        session,
        job_type=JobType.NOTE_METADATA.value,
        payload={"note_id": 1},
        priority=10,
    )
    session.commit()

    job = claim_next_job(session, worker_id="worker:test")

    assert job is not None
    assert job.status == JobStatus.RUNNING.value
    assert job.locked_by == "worker:test"
    assert job.attempts == 1


def test_recover_stale_running_jobs(session):
    stale_now = utc_now()
    job = enqueue_job(
        session,
        job_type=JobType.NOTE_METADATA.value,
        payload={"note_id": 1},
    )
    job.status = JobStatus.RUNNING.value
    job.locked_at = stale_now - timedelta(minutes=30)
    job.locked_by = "worker:dead"
    session.add(job)
    session.commit()

    recovered = recover_stale_running_jobs(
        session,
        timeout_seconds=600,
        now=stale_now,
    )

    session.refresh(job)
    assert recovered == 1
    assert job.status == JobStatus.PENDING.value
    assert job.locked_at is None
    assert job.locked_by is None
