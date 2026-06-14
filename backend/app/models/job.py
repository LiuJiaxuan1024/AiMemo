from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.note import utc_now


class Job(SQLModel, table=True):
    # 详见 Note.__table_args__ 的注释。
    __table_args__ = {"sqlite_autoincrement": True}

    id: int | None = Field(default=None, primary_key=True)
    type: str = Field(index=True, max_length=80)
    graph_name: str | None = Field(default=None, index=True, max_length=120)
    thread_id: str | None = Field(default=None, index=True, max_length=120)
    dedupe_key: str | None = Field(default=None, index=True, max_length=200)
    lane: str = Field(default="default", index=True, max_length=80)
    lock_key: str | None = Field(default=None, index=True, max_length=240)
    concurrency_policy: str = Field(default="exclusive", index=True, max_length=24)
    resource_weight: int = Field(default=1)
    status: str = Field(default="pending", index=True, max_length=24)
    payload: str = "{}"
    priority: int = Field(default=0, index=True)
    attempts: int = 0
    max_attempts: int = 3
    error: str = ""
    locked_at: datetime | None = Field(default=None, index=True)
    locked_by: str | None = Field(default=None, index=True, max_length=120)
    run_after: datetime = Field(default_factory=utc_now, index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now, index=True)
    completed_at: datetime | None = Field(default=None, index=True)
