from __future__ import annotations

import threading
import time

from sqlmodel import Session, select

from app.jobs.models import JobStatus, JobType
from app.jobs.queue import enqueue_job
from app.jobs.worker import JobWorker
from app.models.job import Job


def test_job_worker_runs_different_locks_concurrently(session_factory) -> None:
    with session_factory() as session:
        enqueue_job(session, job_type=JobType.NOTE_METADATA.value, payload={"note_id": 1})
        enqueue_job(session, job_type=JobType.NOTE_METADATA.value, payload={"note_id": 2})
        session.commit()

    lock = threading.Lock()
    running = 0
    max_running = 0

    def handler(job: Job) -> None:
        nonlocal running, max_running
        with lock:
            running += 1
            max_running = max(max_running, running)
        time.sleep(0.12)
        with lock:
            running -= 1

    worker = JobWorker(
        session_factory=session_factory,
        handlers={JobType.NOTE_METADATA.value: handler},
        poll_interval_seconds=0.01,
        running_timeout_seconds=600,
        max_concurrency=2,
        worker_id="worker:test",
    )
    worker.start()
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with session_factory() as session:
                statuses = session.exec(select(Job.status)).all()
            if statuses and all(status == JobStatus.COMPLETED.value for status in statuses):
                break
            time.sleep(0.02)
    finally:
        worker.stop()

    assert max_running == 2
    with session_factory() as session:
        jobs = session.exec(select(Job)).all()
    assert {job.status for job in jobs} == {JobStatus.COMPLETED.value}


def test_job_worker_keeps_same_lock_serial(session_factory) -> None:
    with session_factory() as session:
        enqueue_job(
            session,
            job_type=JobType.NOTE_METADATA.value,
            payload={"note_id": 1},
            dedupe_key="note_metadata:note:1:first",
        )
        enqueue_job(
            session,
            job_type=JobType.NOTE_METADATA.value,
            payload={"note_id": 1},
            dedupe_key="note_metadata:note:1:second",
        )
        session.commit()

    lock = threading.Lock()
    running = 0
    max_running = 0

    def handler(job: Job) -> None:
        nonlocal running, max_running
        with lock:
            running += 1
            max_running = max(max_running, running)
        time.sleep(0.08)
        with lock:
            running -= 1

    worker = JobWorker(
        session_factory=session_factory,
        handlers={JobType.NOTE_METADATA.value: handler},
        poll_interval_seconds=0.01,
        running_timeout_seconds=600,
        max_concurrency=2,
        worker_id="worker:test",
    )
    worker.start()
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with session_factory() as session:
                statuses = session.exec(select(Job.status)).all()
            if statuses and all(status == JobStatus.COMPLETED.value for status in statuses):
                break
            time.sleep(0.02)
    finally:
        worker.stop()

    assert max_running == 1
    with session_factory() as session:
        jobs = session.exec(select(Job)).all()
    assert {job.status for job in jobs} == {JobStatus.COMPLETED.value}
