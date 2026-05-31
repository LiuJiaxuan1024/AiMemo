from fastapi import APIRouter, Depends, Query, status
from sqlmodel import Session

from app.core.database import get_session
from app.schemas.job import JobGraphRead, JobRead
from app.services.job_service import delete_job, get_job, get_job_graph, list_jobs, retry_job


router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("", response_model=list[JobRead])
def list_jobs_api(
    limit: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> list[JobRead]:
    return list_jobs(session, limit=limit)


@router.get("/{job_id}", response_model=JobRead)
def get_job_api(
    job_id: int,
    session: Session = Depends(get_session),
) -> JobRead:
    return get_job(session, job_id)


@router.get("/{job_id}/graph", response_model=JobGraphRead)
def get_job_graph_api(
    job_id: int,
    session: Session = Depends(get_session),
) -> JobGraphRead:
    return get_job_graph(session, job_id)


@router.post("/{job_id}/retry", response_model=JobRead)
def retry_job_api(
    job_id: int,
    session: Session = Depends(get_session),
) -> JobRead:
    return retry_job(session, job_id)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_job_api(
    job_id: int,
    session: Session = Depends(get_session),
) -> None:
    delete_job(session, job_id)
