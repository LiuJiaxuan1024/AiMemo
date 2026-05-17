from datetime import datetime, timedelta, timezone
from typing import Any

from sqlmodel import Session, col, desc, select

from app.jobs.models import JobStatus
from app.jobs.payloads import encode_payload
from app.models.job import Job


ACTIVE_STATUSES = {JobStatus.PENDING.value, JobStatus.RUNNING.value}


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def enqueue_job(
    session: Session,
    *,
    job_type: str,
    payload: dict[str, Any],
    graph_name: str | None = None,
    dedupe_key: str | None = None,
    priority: int = 0,
    max_attempts: int = 3,
) -> Job:
    if dedupe_key:
        # 避免同一个业务对象堆积重复的活跃任务，例如同一条 note 同时存在两个 metadata job。
        existing = session.exec(
            select(Job).where(
                Job.dedupe_key == dedupe_key,
                col(Job.status).in_(ACTIVE_STATUSES),
            )
        ).first()
        if existing:
            return existing

    job = Job(
        type=job_type,
        graph_name=graph_name,
        dedupe_key=dedupe_key,
        payload=encode_payload(payload),
        priority=priority,
        max_attempts=max_attempts,
    )
    session.add(job)
    session.flush()
    if graph_name and job.id is not None:
        # 把应用层 job 绑定到 graph 层 checkpoint thread。
        job.thread_id = f"job:{job.id}"
        session.add(job)
        session.flush()
    return job


def claim_next_job(session: Session, *, worker_id: str, now: datetime | None = None) -> Job | None:
    current_time = now or utc_now()
    # SQLite 是本地持久化队列。当前单 worker 执行足够简单；
    # priority/run_after 已经为后续扩展优先级和延迟任务留好形状。
    job = session.exec(
        select(Job)
        .where(Job.status == JobStatus.PENDING.value, Job.run_after <= current_time)
        .order_by(desc(Job.priority), Job.created_at)
    ).first()
    if job is None:
        return None

    job.status = JobStatus.RUNNING.value
    job.locked_at = current_time
    job.locked_by = worker_id
    job.attempts += 1
    job.updated_at = current_time
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def complete_job(session: Session, job: Job, *, now: datetime | None = None) -> Job:
    current_time = now or utc_now()
    job.status = JobStatus.COMPLETED.value
    job.error = ""
    job.locked_at = None
    job.locked_by = None
    job.completed_at = current_time
    job.updated_at = current_time
    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def fail_job(session: Session, job: Job, error: str, *, now: datetime | None = None) -> Job:
    current_time = now or utc_now()
    job.error = error[:4000]
    job.locked_at = None
    job.locked_by = None
    job.updated_at = current_time

    if job.attempts < job.max_attempts:
        # 重试需要延迟，避免模型或网络的瞬时失败导致 worker 空转。
        job.status = JobStatus.PENDING.value
        job.run_after = current_time + _retry_delay(job.attempts)
    else:
        job.status = JobStatus.FAILED.value

    session.add(job)
    session.commit()
    session.refresh(job)
    return job


def recover_stale_running_jobs(
    session: Session,
    *,
    timeout_seconds: int,
    now: datetime | None = None,
) -> int:
    current_time = now or utc_now()
    stale_before = current_time - timedelta(seconds=timeout_seconds)
    # 如果进程在 job running 时退出，启动/轮询时会把超时任务恢复为 pending。
    # 随后 LangGraph checkpoint 决定 graph 内部应该从哪个节点继续。
    jobs = session.exec(
        select(Job).where(
            Job.status == JobStatus.RUNNING.value,
            Job.locked_at != None,  # noqa: E711
            Job.locked_at < stale_before,
        )
    ).all()

    for job in jobs:
        job.status = JobStatus.PENDING.value
        job.locked_at = None
        job.locked_by = None
        job.updated_at = current_time
        session.add(job)

    session.commit()
    return len(jobs)


def _retry_delay(attempts: int) -> timedelta:
    if attempts <= 1:
        return timedelta(seconds=30)
    if attempts == 2:
        return timedelta(minutes=2)
    return timedelta(minutes=10)
