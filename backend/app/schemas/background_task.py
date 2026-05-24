from datetime import datetime

from pydantic import BaseModel


class BackgroundTaskRead(BaseModel):
    task_id: str
    conversation_id: int | None
    command: str
    cwd: str
    pid: int | None
    status: str
    exit_code: int | None
    kill_reason: str
    started_at: datetime
    finished_at: datetime | None


class BackgroundTaskOutputLine(BaseModel):
    line: int
    stream: str
    text: str


class BackgroundTaskOutputRead(BaseModel):
    task_id: str
    status: str
    pid: int | None
    exit_code: int | None
    lines: list[BackgroundTaskOutputLine]
    last_line: int
    dropped_lines: int
    more: bool
