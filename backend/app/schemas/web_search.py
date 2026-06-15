from pydantic import BaseModel, Field


class WebSearchRequest(BaseModel):
    query: str = Field(min_length=1)
    max_results: int = Field(default=5, ge=1, le=10)
    freshness: str = Field(default="any")
    locale: str = Field(default="zh-CN")
    site: str = Field(default="")
    provider: str = Field(default="")
    model: str = Field(default="")
    search_strategy: str = Field(default="basic")


class WebSearchResultItem(BaseModel):
    title: str = ""
    url: str
    snippet: str = ""
    source_domain: str = ""
    published_at: str | None = None
    rank: int = 0
    fetched: bool = False
    fetch_title: str = ""
    fetch_text_preview: str = ""


class WebSearchResponse(BaseModel):
    ok: bool
    provider: str = ""
    query: str = ""
    results: list[WebSearchResultItem] = Field(default_factory=list)
    conclusion: str = ""
    cached: bool = False
    error_code: str = ""
    message: str = ""
    raw_usage: dict = Field(default_factory=dict)


class WebFetchRequest(BaseModel):
    url: str
    max_chars: int = Field(default=12000, ge=100, le=50000)


class WebFetchResponse(BaseModel):
    ok: bool
    url: str
    title: str = ""
    text: str = ""
    content_type: str = ""
    fetched_at: str = ""
    truncated: bool = False
    error_code: str = ""
    message: str = ""
