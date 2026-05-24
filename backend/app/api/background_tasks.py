from fastapi import APIRouter, Depends, Query, Response, status
from sqlmodel import Session

from app.core.database import get_session
from app.schemas.background_task import BackgroundTaskOutputRead, BackgroundTaskRead
from app.services.background_task_service import (
    get_background_task,
    get_background_task_output,
    kill_background_task,
    list_background_tasks,
    prune_background_task,
)


router = APIRouter(prefix="/background_tasks", tags=["background_tasks"])


@router.get("", response_model=list[BackgroundTaskRead])
def list_background_tasks_api(
    conversation_id: int | None = Query(default=None),
    include_finished: bool = Query(default=True),
    limit: int = Query(default=100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> list[BackgroundTaskRead]:
    return list_background_tasks(
        session,
        conversation_id=conversation_id,
        include_finished=include_finished,
        limit=limit,
    )


@router.get("/{task_id}", response_model=BackgroundTaskRead)
def get_background_task_api(
    task_id: str,
    session: Session = Depends(get_session),
) -> BackgroundTaskRead:
    return get_background_task(session, task_id)


@router.get("/{task_id}/output", response_model=BackgroundTaskOutputRead)
def get_background_task_output_api(
    task_id: str,
    since_line: int = Query(default=0, ge=0),
    max_lines: int = Query(default=50, ge=1, le=200),
    session: Session = Depends(get_session),
) -> BackgroundTaskOutputRead:
    return get_background_task_output(
        session,
        task_id,
        since_line=since_line,
        max_lines=max_lines,
    )


@router.post("/{task_id}/kill", response_model=BackgroundTaskRead)
def kill_background_task_api(
    task_id: str,
    session: Session = Depends(get_session),
) -> BackgroundTaskRead:
    return kill_background_task(session, task_id)


@router.delete("/{task_id}", status_code=status.HTTP_204_NO_CONTENT)
def prune_background_task_api(
    task_id: str,
    session: Session = Depends(get_session),
) -> Response:
    prune_background_task(session, task_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
