from datetime import date, datetime

from sqlmodel import Field, SQLModel, UniqueConstraint

from app.models.note import utc_now


class WebSearchCache(SQLModel, table=True):
    """Cached normalized public web search results."""

    __tablename__ = "web_search_cache"
    __table_args__ = (
        UniqueConstraint("provider", "query_hash", "locale", "freshness", "site", name="uq_web_search_cache_key"),
        {"sqlite_autoincrement": True},
    )

    id: int | None = Field(default=None, primary_key=True)
    provider: str = Field(index=True, max_length=80)
    query_hash: str = Field(index=True, max_length=80)
    query_text: str = Field(default="", max_length=1000)
    locale: str = Field(default="zh-CN", index=True, max_length=40)
    freshness: str = Field(default="any", index=True, max_length=40)
    site: str = Field(default="", index=True, max_length=200)
    results_json: str = Field(default="{}")
    created_at: datetime = Field(default_factory=utc_now, index=True)
    expires_at: datetime = Field(index=True)


class WebSearchUsage(SQLModel, table=True):
    """Daily web search usage counter by provider."""

    __tablename__ = "web_search_usage"
    __table_args__ = (
        UniqueConstraint("provider", "usage_date", name="uq_web_search_usage_provider_date"),
        {"sqlite_autoincrement": True},
    )

    id: int | None = Field(default=None, primary_key=True)
    provider: str = Field(index=True, max_length=80)
    usage_date: date = Field(index=True)
    request_count: int = Field(default=0)
    created_at: datetime = Field(default_factory=utc_now, index=True)
    updated_at: datetime = Field(default_factory=utc_now, index=True)


class WebSearchEvent(SQLModel, table=True):
    """Auditable web search/fetch event metadata."""

    __tablename__ = "web_search_events"
    __table_args__ = {"sqlite_autoincrement": True}

    id: int | None = Field(default=None, primary_key=True)
    conversation_id: int = Field(default=0, index=True)
    provider: str = Field(default="", index=True, max_length=80)
    tool_name: str = Field(default="", index=True, max_length=80)
    query_hash: str = Field(default="", index=True, max_length=80)
    query_preview: str = Field(default="", max_length=240)
    result_count: int = Field(default=0)
    cached: bool = Field(default=False, index=True)
    error_code: str = Field(default="", index=True, max_length=120)
    created_at: datetime = Field(default_factory=utc_now, index=True)
