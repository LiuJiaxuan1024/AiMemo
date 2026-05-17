from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.note import utc_now


class NoteChunk(SQLModel, table=True):
    id: int | None = Field(default=None, primary_key=True)
    note_id: int = Field(index=True)
    chunk_index: int = Field(index=True)
    content: str
    content_hash: str = Field(index=True, max_length=64)
    token_count: int = Field(default=0, index=True)
    embedding_status: str = Field(default="pending", index=True, max_length=24)
    embedding_error: str = ""
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now, index=True)
