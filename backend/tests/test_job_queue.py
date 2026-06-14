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
    assert job.lane == "note_light"
    assert job.lock_key == "note:1"
    assert job.concurrency_policy == "shared"


def test_claim_next_job_skips_locked_resource_and_claims_other_lane(session):
    first = enqueue_job(
        session,
        job_type=JobType.NOTE_METADATA.value,
        payload={"note_id": 1},
        priority=10,
    )
    blocked = enqueue_job(
        session,
        job_type=JobType.NOTE_METADATA.value,
        payload={"note_id": 1},
        priority=9,
        dedupe_key="note_metadata:note:1:second",
    )
    runnable = enqueue_job(
        session,
        job_type=JobType.NOTE_METADATA.value,
        payload={"note_id": 2},
        priority=1,
    )
    session.commit()

    claimed_first = claim_next_job(session, worker_id="worker:test", max_running=3)
    claimed_second = claim_next_job(session, worker_id="worker:test", max_running=3)

    assert claimed_first is not None
    assert claimed_first.id == first.id
    assert blocked.id is not None
    assert claimed_second is not None
    assert claimed_second.id == runnable.id


def test_claim_next_job_respects_lane_concurrency(session):
    enqueue_job(
        session,
        job_type=JobType.NOTE_EMBEDDING.value,
        payload={"note_id": 1},
        priority=10,
    )
    blocked_embedding = enqueue_job(
        session,
        job_type=JobType.NOTE_EMBEDDING.value,
        payload={"note_id": 2},
        priority=9,
    )
    metadata = enqueue_job(
        session,
        job_type=JobType.NOTE_METADATA.value,
        payload={"note_id": 3},
        priority=1,
    )
    session.commit()

    claimed_first = claim_next_job(session, worker_id="worker:test", max_running=3)
    claimed_second = claim_next_job(session, worker_id="worker:test", max_running=3)

    assert claimed_first is not None
    assert claimed_first.lane == "embedding"
    assert blocked_embedding.id is not None
    assert claimed_second is not None
    assert claimed_second.id == metadata.id


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
