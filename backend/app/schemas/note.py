from datetime import datetime

from pydantic import BaseModel, Field


class NoteCreate(BaseModel):
    title: str = Field(default="", max_length=200)
    content: str = Field(min_length=1)
    summary: str = ""
    tags: list[str] = Field(default_factory=list)


class NoteRead(BaseModel):
    id: int
    title: str
    content: str
    summary: str
    tags: list[str]
    processing_status: str
    processing_error: str
    processed_at: datetime | None
    embedding_status: str
    embedding_error: str
    embedded_at: datetime | None
    created_at: datetime
    updated_at: datetime


class NoteListItem(BaseModel):
    id: int
    title: str
    summary: str
    tags: list[str]
    processing_status: str
    processing_error: str
    processed_at: datetime | None
    embedding_status: str
    embedding_error: str
    embedded_at: datetime | None
    created_at: datetime
    updated_at: datetime
