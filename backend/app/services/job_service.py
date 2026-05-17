from fastapi import HTTPException, status
from sqlmodel import Session, desc, select

from app.agent.graphs.registry import get_job_graph_view
from app.jobs.payloads import decode_payload
from app.models.job import Job
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
