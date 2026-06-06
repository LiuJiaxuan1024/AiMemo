from datetime import datetime

from sqlmodel import Field, SQLModel, UniqueConstraint

from app.models.note import utc_now


class KnowledgeSpace(SQLModel, table=True):
    """A user-managed knowledge space that can be mounted into conversations."""

    __table_args__ = {"sqlite_autoincrement": True}

    id: int | None = Field(default=None, primary_key=True)
    name: str = Field(index=True, max_length=120)
    description: str = ""
    icon: str | None = Field(default=None, max_length=80)
    status: str = Field(default="active", index=True, max_length=24)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now, index=True)


class KnowledgeDocument(SQLModel, table=True):
    """A source document registered under a knowledge space."""

    __table_args__ = {"sqlite_autoincrement": True}

    id: int | None = Field(default=None, primary_key=True)
    space_id: int = Field(foreign_key="knowledgespace.id", index=True)
    title: str = Field(index=True, max_length=240)
    source_type: str = Field(default="file", index=True, max_length=24)
    source_uri: str | None = Field(default=None, max_length=1000)
    storage_path: str | None = Field(default=None, max_length=1000)
    original_filename: str | None = Field(default=None, max_length=240)
    mime_type: str | None = Field(default=None, max_length=120)
    content_hash: str = Field(default="", index=True, max_length=64)
    parser: str | None = Field(default=None, max_length=80)
    chunk_strategy: str = Field(default="heading_paragraph_token", max_length=80)
    status: str = Field(default="pending", index=True, max_length=24)
    chunk_count: int = Field(default=0)
    text_chunk_count: int = Field(default=0)
    image_asset_count: int = Field(default=0)
    image_asset_processed_count: int = Field(default=0)
    image_text_chunk_count: int = Field(default=0)
    image_asset_failed_count: int = Field(default=0)
    token_count: int = Field(default=0)
    error_code: str | None = Field(default=None, max_length=80)
    error_message: str | None = None
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now, index=True)
    processed_at: datetime | None = Field(default=None, index=True)


class KnowledgeChunk(SQLModel, table=True):
    """A searchable chunk derived from a knowledge document."""

    __table_args__ = {"sqlite_autoincrement": True}

    id: int | None = Field(default=None, primary_key=True)
    space_id: int = Field(foreign_key="knowledgespace.id", index=True)
    document_id: int = Field(foreign_key="knowledgedocument.id", index=True)
    chunk_index: int = Field(index=True)
    text: str
    summary: str | None = None
    heading_path: str | None = None
    page_number: int | None = Field(default=None, index=True)
    source_offset: int | None = None
    token_count: int = Field(default=0)
    content_hash: str = Field(default="", index=True, max_length=64)
    embedding_status: str = Field(default="pending", index=True, max_length=24)
    embedding_error: str | None = None
    metadata_json: str | None = None
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now, index=True)


class ConversationKnowledgeMount(SQLModel, table=True):
    """Explicit mount scope that allows a conversation to search a knowledge space."""

    __table_args__ = (
        UniqueConstraint("conversation_id", "space_id", name="uq_conversation_knowledge_mount"),
        {"sqlite_autoincrement": True},
    )

    id: int | None = Field(default=None, primary_key=True)
    conversation_id: int = Field(foreign_key="conversation.id", index=True)
    space_id: int = Field(foreign_key="knowledgespace.id", index=True)
    created_by: str = Field(default="user", max_length=24)
    scope_note: str | None = None
    created_at: datetime = Field(default_factory=utc_now, index=True)
