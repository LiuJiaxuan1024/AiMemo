from datetime import datetime

from sqlmodel import Field, SQLModel, UniqueConstraint

from app.models.note import utc_now


class SyncState(SQLModel, table=True):
    """Local sync cursor for a provider/user namespace."""

    __tablename__ = "sync_states"
    __table_args__ = (
        UniqueConstraint("provider", "user_id", "manifest_key", name="uq_sync_state_provider_user_manifest"),
        {"sqlite_autoincrement": True},
    )

    id: int | None = Field(default=None, primary_key=True)
    provider: str = Field(index=True, max_length=40)
    user_id: str = Field(index=True, max_length=120)
    manifest_key: str = Field(index=True, max_length=500)
    last_remote_global_revision: int = Field(default=0, index=True)
    last_manifest_etag: str = Field(default="", max_length=120)
    last_pull_at: datetime | None = Field(default=None, index=True)
    last_push_at: datetime | None = Field(default=None, index=True)
    last_error: str = ""
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now, index=True)
