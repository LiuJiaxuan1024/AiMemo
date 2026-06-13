from datetime import datetime

from sqlmodel import Field, SQLModel

from app.models.note import utc_now


class CloudObject(SQLModel, table=True):
    """Metadata for files stored by a cloud object provider."""

    __tablename__ = "cloud_objects"
    __table_args__ = {"sqlite_autoincrement": True}

    id: int | None = Field(default=None, primary_key=True)
    owner_user_id: str = Field(default="", index=True, max_length=120)
    provider: str = Field(default="", index=True, max_length=40)
    bucket: str = Field(default="", index=True, max_length=160)
    region: str = Field(default="", index=True, max_length=80)
    object_key: str = Field(index=True, max_length=500)
    storage_class: str = Field(default="Standard", index=True, max_length=40)
    content_type: str = Field(default="application/octet-stream", max_length=120)
    size_bytes: int = Field(default=0, index=True)
    sha256: str = Field(default="", index=True, max_length=80)
    original_filename: str = Field(default="", max_length=255)
    status: str = Field(default="active", index=True, max_length=24)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    uploaded_at: datetime | None = Field(default=None, index=True)
    last_accessed_at: datetime | None = Field(default=None, index=True)
