from datetime import datetime, timedelta, timezone
from dataclasses import dataclass
from typing import Any

from sqlmodel import Session, col, desc, select

from app.core.config import settings
from app.jobs.models import JobStatus, JobType
from app.jobs.payloads import encode_payload
from app.models.job import Job


ACTIVE_STATUSES = {JobStatus.PENDING.value, JobStatus.RUNNING.value}
SHARED_POLICY = "shared"
EXCLUSIVE_POLICY = "exclusive"
DEFAULT_LANE = "default"
DEFAULT_RESOURCE_WEIGHT = 1
LANE_MAX_CONCURRENCY = {
    "note_light": 2,
    "embedding": 1,
    "knowledge_ingest": 1,
    "knowledge_retry": 2,
    "conversation_maintenance": 1,
    "cloud_sync": 1,
    DEFAULT_LANE: 1,
}


@dataclass(frozen=True)
class JobScheduling:
    lane: str
    lock_key: str | None
    concurrency_policy: str
    resource_weight: int = DEFAULT_RESOURCE_WEIGHT


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
    lane: str | None = None,
    lock_key: str | None = None,
    concurrency_policy: str | None = None,
    resource_weight: int | None = None,
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

    scheduling = derive_job_scheduling(
        job_type=job_type,
        payload=payload,
        lane=lane,
        lock_key=lock_key,
        concurrency_policy=concurrency_policy,
        resource_weight=resource_weight,
    )
    job = Job(
        type=job_type,
        graph_name=graph_name,
        dedupe_key=dedupe_key,
        lane=scheduling.lane,
        lock_key=scheduling.lock_key,
        concurrency_policy=scheduling.concurrency_policy,
        resource_weight=scheduling.resource_weight,
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


def claim_next_job(
    session: Session,
    *,
    worker_id: str,
    now: datetime | None = None,
    max_running: int | None = None,
) -> Job | None:
    current_time = now or utc_now()
    running_jobs = session.exec(select(Job).where(Job.status == JobStatus.RUNNING.value)).all()
    max_running_jobs = max_running if max_running is not None else max(1, int(settings.job_worker_concurrency))
    if len(running_jobs) >= max_running_jobs:
        return None

    pending_jobs = session.exec(
        select(Job)
        .where(Job.status == JobStatus.PENDING.value, Job.run_after <= current_time)
        .order_by(desc(Job.priority), Job.created_at)
        .limit(100)
    ).all()
    job = _first_runnable_job(pending_jobs, running_jobs)
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


def derive_job_scheduling(
    *,
    job_type: str,
    payload: dict[str, Any],
    lane: str | None = None,
    lock_key: str | None = None,
    concurrency_policy: str | None = None,
    resource_weight: int | None = None,
) -> JobScheduling:
    inferred = _infer_job_scheduling(job_type, payload)
    return JobScheduling(
        lane=_normalize_lane(lane or inferred.lane),
        lock_key=lock_key if lock_key is not None else inferred.lock_key,
        concurrency_policy=_normalize_concurrency_policy(concurrency_policy or inferred.concurrency_policy),
        resource_weight=max(1, int(resource_weight if resource_weight is not None else inferred.resource_weight)),
    )


def lane_max_concurrency(lane: str) -> int:
    return max(1, int(LANE_MAX_CONCURRENCY.get(_normalize_lane(lane), LANE_MAX_CONCURRENCY[DEFAULT_LANE])))


def _first_runnable_job(pending_jobs: list[Job], running_jobs: list[Job]) -> Job | None:
    for job in pending_jobs:
        if _is_job_runnable(job, running_jobs):
            return job
    return None


def _is_job_runnable(job: Job, running_jobs: list[Job]) -> bool:
    lane = _job_lane(job)
    if _running_lane_count(running_jobs, lane) >= lane_max_concurrency(lane):
        return False
    lock_key = _job_lock_key(job)
    if lock_key and any(_job_lock_key(running_job) == lock_key for running_job in running_jobs):
        return False
    policy = _job_policy(job)
    if policy == EXCLUSIVE_POLICY and any(_job_lane(running_job) == lane for running_job in running_jobs):
        return False
    if any(_job_lane(running_job) == lane and _job_policy(running_job) == EXCLUSIVE_POLICY for running_job in running_jobs):
        return False
    return True


def _running_lane_count(running_jobs: list[Job], lane: str) -> int:
    return sum(1 for running_job in running_jobs if _job_lane(running_job) == lane)


def _infer_job_scheduling(job_type: str, payload: dict[str, Any]) -> JobScheduling:
    if job_type == JobType.NOTE_METADATA.value:
        return JobScheduling(lane="note_light", lock_key=_entity_lock("note", payload.get("note_id")), concurrency_policy=SHARED_POLICY)
    if job_type == JobType.NOTE_EMBEDDING.value:
        return JobScheduling(lane="embedding", lock_key=_entity_lock("note", payload.get("note_id")), concurrency_policy=SHARED_POLICY)
    if job_type == JobType.KNOWLEDGE_INGEST.value:
        return JobScheduling(lane="knowledge_ingest", lock_key=_entity_lock("document", payload.get("document_id")), concurrency_policy=SHARED_POLICY)
    if job_type == JobType.KNOWLEDGE_IMAGE_RETRY.value:
        return JobScheduling(lane="knowledge_retry", lock_key=_entity_lock("document", payload.get("document_id")), concurrency_policy=SHARED_POLICY)
    if job_type in {
        JobType.CONVERSATION_SUMMARY.value,
        JobType.CONVERSATION_MEMORY.value,
        JobType.CONVERSATION_TITLE.value,
    }:
        return JobScheduling(
            lane="conversation_maintenance",
            lock_key=_entity_lock("conversation", payload.get("conversation_id")),
            concurrency_policy=SHARED_POLICY,
        )
    return JobScheduling(lane=DEFAULT_LANE, lock_key=None, concurrency_policy=EXCLUSIVE_POLICY)


def _entity_lock(prefix: str, raw_id: object) -> str | None:
    try:
        value = int(raw_id)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return f"{prefix}:{value}"


def _job_lane(job: Job) -> str:
    return _normalize_lane(job.lane)


def _job_lock_key(job: Job) -> str | None:
    return job.lock_key or None


def _job_policy(job: Job) -> str:
    return _normalize_concurrency_policy(job.concurrency_policy)


def _normalize_lane(value: str | None) -> str:
    lane = (value or DEFAULT_LANE).strip()
    return lane or DEFAULT_LANE


def _normalize_concurrency_policy(value: str | None) -> str:
    policy = (value or EXCLUSIVE_POLICY).strip().lower()
    return SHARED_POLICY if policy == SHARED_POLICY else EXCLUSIVE_POLICY


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
