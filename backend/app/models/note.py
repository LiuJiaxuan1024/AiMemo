from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Note(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    title: str = Field(default="", index=True, max_length=200)
    title_source: str = Field(default="fallback", index=True, max_length=24)
    content: str
    summary: str = ""
    tags: str = ""
    processing_status: str = Field(default="pending", index=True, max_length=24)
    processing_error: str = ""
    processed_at: datetime | None = Field(default=None, index=True)
    embedding_status: str = Field(default="pending", index=True, max_length=24)
    embedding_error: str = ""
    embedded_at: datetime | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now, index=True)
