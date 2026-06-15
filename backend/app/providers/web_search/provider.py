from typing import Protocol

from app.schemas.web_search import WebSearchRequest, WebSearchResponse


class WebSearchProvider(Protocol):
    """Provider interface for normalized public web search."""

    name: str

    def search(self, request: WebSearchRequest) -> WebSearchResponse:
        ...
