from datetime import datetime

from sqlmodel import Field, SQLModel, UniqueConstraint

from app.models.note import utc_now


class SyncItem(SQLModel, table=True):
    """Per-domain local cursor for one synced business entity."""

    __tablename__ = "sync_items"
    __table_args__ = (
        UniqueConstraint("provider", "user_id", "domain", "entity_id", name="uq_sync_item_entity"),
        {"sqlite_autoincrement": True},
    )

    id: int | None = Field(default=None, primary_key=True)
    provider: str = Field(index=True, max_length=40)
    user_id: str = Field(index=True, max_length=120)
    domain: str = Field(index=True, max_length=40)
    entity_id: str = Field(index=True, max_length=120)
    local_revision: int = Field(default=1, index=True)
    cloud_revision: int = Field(default=0, index=True)
    last_synced_revision: int = Field(default=0, index=True)
    content_hash: str = Field(default="", index=True, max_length=80)
    status: str = Field(default="dirty", index=True, max_length=24)
    object_key: str = Field(default="", index=True, max_length=500)
    last_synced_at: datetime | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now, index=True)


class SyncConflict(SQLModel, table=True):
    """A conflict detected between local dirty data and newer cloud data."""

    __tablename__ = "sync_conflicts"
    __table_args__ = (
        UniqueConstraint("provider", "user_id", "domain", "entity_id", "remote_revision", name="uq_sync_conflict_remote"),
        {"sqlite_autoincrement": True},
    )

    id: int | None = Field(default=None, primary_key=True)
    provider: str = Field(index=True, max_length=40)
    user_id: str = Field(index=True, max_length=120)
    domain: str = Field(index=True, max_length=40)
    entity_id: str = Field(index=True, max_length=120)
    local_revision: int = Field(default=0, index=True)
    remote_revision: int = Field(default=0, index=True)
    conflict_type: str = Field(default="remote_changed_local_modified", index=True, max_length=80)
    local_summary: str = ""
    remote_summary: str = ""
    remote_object_key: str = Field(default="", max_length=500)
    status: str = Field(default="open", index=True, max_length=24)
    resolution: str = Field(default="keep_both", index=True, max_length=40)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now, index=True)


class SyncDevice(SQLModel, table=True):
    """A device observed in cloud sync manifests."""

    __tablename__ = "sync_devices"
    __table_args__ = (
        UniqueConstraint("provider", "user_id", "device_id", name="uq_sync_device"),
        {"sqlite_autoincrement": True},
    )

    id: int | None = Field(default=None, primary_key=True)
    provider: str = Field(index=True, max_length=40)
    user_id: str = Field(index=True, max_length=120)
    device_id: str = Field(index=True, max_length=120)
    device_name: str = Field(default="", max_length=160)
    last_seen_at: datetime | None = Field(default=None, index=True)
    last_pull_at: datetime | None = Field(default=None, index=True)
    last_push_at: datetime | None = Field(default=None, index=True)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now, index=True)
