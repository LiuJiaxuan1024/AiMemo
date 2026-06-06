from fastapi import HTTPException, status
from sqlmodel import Session, desc, select

from app.jobs.models import JobStatus
from app.jobs.payloads import decode_payload
from app.models.job import Job
from app.models.note import utc_now
from app.schemas.job import JobGraphRead, JobRead


def list_jobs(session: Session, *, limit: int = 50) -> list[JobRead]:
    jobs = session.exec(select(Job).order_by(desc(Job.created_at)).limit(limit)).all()
    return [_to_job_read(job) for job in jobs]


def get_job(session: Session, job_id: int) -> JobRead:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )
    return _to_job_read(job)


def get_job_graph(session: Session, job_id: int) -> JobGraphRead:
    from app.agent.graphs.registry import get_job_graph_view

    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )
    if not job.graph_name:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job has no graph",
        )

    graph_view = get_job_graph_view(job)
    return JobGraphRead(
        job_id=job.id or 0,
        graph_name=job.graph_name,
        thread_id=job.thread_id or f"job:{job.id}",
        status=job.status,
        next_nodes=graph_view.next_nodes,
        mermaid=graph_view.mermaid,
    )


def retry_job(session: Session, job_id: int) -> JobRead:
    job = _get_job_or_404(session, job_id)
    if job.status not in {JobStatus.FAILED.value, JobStatus.CANCELED.value}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "JOB_NOT_RETRYABLE", "message": "只有失败或已取消的任务可以重试。"},
        )
    current_time = utc_now()
    job.status = JobStatus.PENDING.value
    job.error = ""
    job.locked_at = None
    job.locked_by = None
    job.run_after = current_time
    job.completed_at = None
    job.updated_at = current_time
    session.add(job)
    session.commit()
    session.refresh(job)
    return _to_job_read(job)


def delete_job(session: Session, job_id: int) -> None:
    job = _get_job_or_404(session, job_id)
    if job.status in {JobStatus.PENDING.value, JobStatus.RUNNING.value}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "JOB_ACTIVE", "message": "任务仍在执行或排队，不能删除。"},
        )
    session.delete(job)
    session.commit()


def _get_job_or_404(session: Session, job_id: int) -> Job:
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Job not found",
        )
    return job


def _to_job_read(job: Job) -> JobRead:
    return JobRead(
        id=job.id or 0,
        type=job.type,
        graph_name=job.graph_name,
        thread_id=job.thread_id,
        dedupe_key=job.dedupe_key,
        status=job.status,
        payload=decode_payload(job.payload),
        priority=job.priority,
        attempts=job.attempts,
        max_attempts=job.max_attempts,
        error=job.error,
        locked_at=job.locked_at,
        locked_by=job.locked_by,
        run_after=job.run_after,
        created_at=job.created_at,
        updated_at=job.updated_at,
        completed_at=job.completed_at,
    )
