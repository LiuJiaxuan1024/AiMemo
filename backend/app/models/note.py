from datetime import datetime, timezone

from sqlmodel import Field, SQLModel


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Note(SQLModel, table=True):
    # sqlite_autoincrement=True 让 SQLite 输出 INTEGER PRIMARY KEY AUTOINCREMENT，
    # 确保 id 严格单调递增、删除最大 id 后**不复用**——避免
    # in-memory cache（如 chat_turn_buffer）与前端 store 按整型 id 索引时的脏数据复用。
    __table_args__ = {"sqlite_autoincrement": True}

    id: int | None = Field(default=None, primary_key=True)
    title: str = Field(default="", index=True, max_length=200)
    title_source: str = Field(default="fallback", index=True, max_length=24)
    content: str
    content_markdown: str = ""
    content_blocks: str = ""
    content_format: str = Field(default="markdown", index=True, max_length=24)
    content_version: int = Field(default=1, index=True)
    content_hash: str = Field(default="", index=True, max_length=64)
    summary: str = ""
    tags: str = ""
    category_id: int | None = Field(default=None, foreign_key="notecategory.id", index=True)
    is_favorite: bool = Field(default=False, index=True)
    pinned_at: datetime | None = Field(default=None, index=True)
    archived_at: datetime | None = Field(default=None, index=True)
    status: str = Field(default="active", index=True, max_length=24)
    processing_status: str = Field(default="pending", index=True, max_length=24)
    processing_error: str = ""
    processed_at: datetime | None = Field(default=None, index=True)
    embedding_status: str = Field(default="pending", index=True, max_length=24)
    embedding_error: str = ""
    embedded_at: datetime | None = Field(default=None, index=True)
    deleted_at: datetime | None = Field(default=None, index=True)
    cloud_revision: int = Field(default=0, index=True)
    local_revision: int = Field(default=1, index=True)
    last_synced_revision: int = Field(default=0, index=True)
    sync_status: str = Field(default="dirty", index=True, max_length=24)
    sync_conflict_id: str = Field(default="", index=True, max_length=80)
    cloud_object_key: str = Field(default="", index=True, max_length=400)
    last_synced_at: datetime | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now, index=True)


class NoteCategory(SQLModel, table=True):
    __table_args__ = {"sqlite_autoincrement": True}

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, max_length=120)
    description: str = ""
    sort_order: int = Field(default=0, index=True)
    color: str = Field(default="", max_length=40)
    status: str = Field(default="active", index=True, max_length=24)
    deleted_at: datetime | None = Field(default=None, index=True)
    cloud_revision: int = Field(default=0, index=True)
    local_revision: int = Field(default=1, index=True)
    last_synced_revision: int = Field(default=0, index=True)
    sync_status: str = Field(default="dirty", index=True, max_length=24)
    sync_conflict_id: str = Field(default="", index=True, max_length=80)
    cloud_object_key: str = Field(default="", index=True, max_length=400)
    last_synced_at: datetime | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now, index=True)
