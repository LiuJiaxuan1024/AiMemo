from datetime import datetime

from pydantic import BaseModel, Field


class NoteCreate(BaseModel):
    title: str = Field(default="", max_length=200)
    content: str | None = Field(default=None, min_length=1)
    content_markdown: str | None = Field(default=None, min_length=1)
    content_blocks: str = ""
    content_format: str = Field(default="markdown", max_length=24)
    summary: str = ""
    tags: list[str] = Field(default_factory=list)
    category_id: int | None = None
    is_favorite: bool = False
    pinned: bool = False


class NoteUpdate(BaseModel):
    """笔记更新请求。

    title/content 都是业务事实；一旦 content 变化，后端会重建 metadata 和 embedding 任务。
    """

    title: str | None = Field(default=None, max_length=200)
    content: str | None = Field(default=None, min_length=1)
    content_markdown: str | None = Field(default=None, min_length=1)
    content_blocks: str | None = None
    content_format: str | None = Field(default=None, max_length=24)
    category_id: int | None = None
    tags: list[str] | None = None
    is_favorite: bool | None = None
    pinned: bool | None = None


class NoteRead(BaseModel):
    id: int
    title: str
    content: str
    content_markdown: str
    content_blocks: str
    content_format: str
    content_version: int
    content_hash: str
    summary: str
    tags: list[str]
    category_id: int | None
    category_name: str
    is_favorite: bool
    pinned_at: datetime | None
    archived_at: datetime | None
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
    category_id: int | None
    category_name: str
    is_favorite: bool
    pinned_at: datetime | None
    archived_at: datetime | None
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


class NoteCategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    color: str = Field(default="", max_length=40)


class NoteCategoryUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    color: str | None = Field(default=None, max_length=40)
    sort_order: int | None = None


class NoteCategoryRead(BaseModel):
    id: int
    name: str
    description: str
    color: str
    sort_order: int
    status: str
    note_count: int
    created_at: datetime
    updated_at: datetime


class NoteTagRead(BaseModel):
    name: str
    note_count: int


class NoteTagRename(BaseModel):
    old_tag: str = Field(min_length=1, max_length=80)
    new_tag: str = Field(min_length=1, max_length=80)


class NoteTagMerge(BaseModel):
    source_tags: list[str] = Field(min_length=1)
    target_tag: str = Field(min_length=1, max_length=80)


class NoteTagDelete(BaseModel):
    tag: str = Field(min_length=1, max_length=80)
