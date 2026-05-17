from datetime import datetime

from pydantic import BaseModel


class JobRead(BaseModel):
    id: int
    type: str
    graph_name: str | None
    thread_id: str | None
    dedupe_key: str | None
    status: str
    payload: dict
    priority: int
    attempts: int
    max_attempts: int
    error: str
    locked_at: datetime | None
    locked_by: str | None
    run_after: datetime
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None


class JobGraphRead(BaseModel):
    job_id: int
    graph_name: str
    thread_id: str
    status: str
    next_nodes: list[str]
    mermaid: str
