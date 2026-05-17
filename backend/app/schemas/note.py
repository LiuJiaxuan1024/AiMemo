from datetime import datetime

from pydantic import BaseModel, Field


class NoteCreate(BaseModel):
    title: str = Field(default="", max_length=200)
    content: str = Field(min_length=1)
    summary: str = ""
    tags: list[str] = Field(default_factory=list)


class NoteUpdate(BaseModel):
    """笔记更新请求。

    title/content 都是业务事实；一旦 content 变化，后端会重建 metadata 和 embedding 任务。
    """

    title: str | None = Field(default=None, max_length=200)
    content: str | None = Field(default=None, min_length=1)


class NoteRead(BaseModel):
    id: int
    title: str
    content: str
    content_hash: str
    summary: str
    tags: list[str]
    status: str
    processing_status: str
    processing_error: str
    processed_at: datetime | None
    embedding_status: str
    embedding_error: str
    embedded_at: datetime | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime


class NoteListItem(BaseModel):
    id: int
    title: str
    content_hash: str
    summary: str
    tags: list[str]
    status: str
    processing_status: str
    processing_error: str
    processed_at: datetime | None
    embedding_status: str
    embedding_error: str
    embedded_at: datetime | None
    deleted_at: datetime | None
    created_at: datetime
    updated_at: datetime
