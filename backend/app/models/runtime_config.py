from datetime import datetime

from sqlmodel import Field, SQLModel, UniqueConstraint

from app.models.note import utc_now


class RuntimeConfig(SQLModel, table=True):
    """User-editable runtime configuration overrides."""

    __tablename__ = "runtime_configs"
    __table_args__ = (
        UniqueConstraint("scope", "path", name="uq_runtime_configs_scope_path"),
        {"sqlite_autoincrement": True},
    )

    id: int | None = Field(default=None, primary_key=True)
    scope: str = Field(default="user", index=True, max_length=40)
    path: str = Field(index=True, max_length=200)
    value_json: str = Field(default="null")
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now, index=True)
