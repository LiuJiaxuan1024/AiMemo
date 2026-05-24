from fastapi import HTTPException, status
from sqlmodel import Session, desc, select

from app.local_operator.background_command import (
    DEFAULT_BACKGROUND_LINES_RETURNED,
    MAX_BACKGROUND_LINES_RETURNED,
    _collect_output_lines,
    pool as background_pool,
)
from app.models.background_task import BackgroundTask
from app.schemas.background_task import (
    BackgroundTaskOutputLine,
    BackgroundTaskOutputRead,
    BackgroundTaskRead,
)


def list_background_tasks(
    session: Session,
    *,
    conversation_id: int | None = None,
    include_finished: bool = True,
    limit: int = 100,
) -> list[BackgroundTaskRead]:
    # 先把内存池里 fresh 任务的状态刷一遍，确保 DB 跟得上（poll 出口码 / 探活）。
    background_pool.list_tasks(conversation_id=conversation_id)

    query = select(BackgroundTask).order_by(desc(BackgroundTask.created_at))
    if conversation_id is not None:
        query = query.where(BackgroundTask.conversation_id == conversation_id)
    records = session.exec(query.limit(limit)).all()
    result: list[BackgroundTaskRead] = []
    for record in records:
        if not include_finished and record.status != "running":
            continue
        result.append(_to_read(record))
    return result


def get_background_task(session: Session, task_id: str) -> BackgroundTaskRead:
    record = _require(session, task_id)
    background_pool.list_tasks()  # 触发状态刷新
    session.refresh(record)
    return _to_read(record)


def get_background_task_output(
    session: Session,
    task_id: str,
    *,
    since_line: int = 0,
    max_lines: int = DEFAULT_BACKGROUND_LINES_RETURNED,
) -> BackgroundTaskOutputRead:
    record = _require(session, task_id)
    # 刷新一遍状态（特别是 adopted 任务的 pid 探活）
    background_pool.list_tasks()
    session.refresh(record)
    capped = max(1, min(int(max_lines or DEFAULT_BACKGROUND_LINES_RETURNED), MAX_BACKGROUND_LINES_RETURNED))
    lines, last_line, more = _collect_output_lines(
        record.stdout_path,
        record.stderr_path,
        since_line=max(0, int(since_line or 0)),
        max_lines=capped,
    )
    return BackgroundTaskOutputRead(
        task_id=record.task_id,
        status=record.status,
        pid=record.pid,
        exit_code=record.exit_code,
        lines=[BackgroundTaskOutputLine(**line) for line in lines],
        last_line=last_line,
        dropped_lines=0,
        more=more,
    )


def kill_background_task(session: Session, task_id: str, *, reason: str = "killed via UI") -> BackgroundTaskRead:
    record = _require(session, task_id)
    result = background_pool.kill(record.task_id, reason=reason)
    if not result.ok and result.blocked:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.message or "kill failed",
        )
    session.refresh(record)
    return _to_read(record)


def prune_background_task(session: Session, task_id: str) -> None:
    record = _require(session, task_id)
    if record.status == "running":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="任务仍在运行，请先终止后再从列表移除。",
        )
    result = background_pool.prune(record.task_id)
    if not result.ok and result.blocked:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.message or "prune failed",
        )


def _require(session: Session, task_id: str) -> BackgroundTask:
    record = session.exec(
        select(BackgroundTask).where(BackgroundTask.task_id == task_id)
    ).first()
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Background task not found",
        )
    return record


def _to_read(record: BackgroundTask) -> BackgroundTaskRead:
    return BackgroundTaskRead(
        task_id=record.task_id,
        conversation_id=record.conversation_id,
        command=record.command,
        cwd=record.cwd,
        pid=record.pid,
        status=record.status,
        exit_code=record.exit_code,
        kill_reason=record.kill_reason or "",
        started_at=record.started_at,
        finished_at=record.finished_at,
    )
