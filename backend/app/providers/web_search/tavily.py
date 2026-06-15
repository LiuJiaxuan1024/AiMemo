from __future__ import annotations

import json
import os
from typing import Any
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

from app.core.config import settings
from app.schemas.web_search import WebSearchRequest, WebSearchResponse, WebSearchResultItem


class TavilyWebSearchProvider:
    """Tavily Search provider for normalized public web search results."""

    name = "tavily"
    endpoint = "https://api.tavily.com/search"

    def __init__(self, *, api_key: str | None = None, timeout_seconds: int | None = None) -> None:
        self.api_key = api_key if api_key is not None else settings.tavily_api_key or os.getenv("TAVILY_API_KEY", "")
        self.timeout_seconds = timeout_seconds or settings.web_search_timeout_seconds

    def search(self, request: WebSearchRequest) -> WebSearchResponse:
        if not self.api_key:
            return WebSearchResponse(
                ok=False,
                provider=self.name,
                query=request.query,
                error_code="WEB_SEARCH_KEY_MISSING",
                message="缺少 TAVILY_API_KEY，无法使用 Tavily 联网搜索。",
            )
        try:
            payload = self._call_tavily(request)
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:1000]
            return WebSearchResponse(
                ok=False,
                provider=self.name,
                query=request.query,
                error_code="WEB_SEARCH_PROVIDER_HTTP_ERROR",
                message=f"Tavily 联网搜索 HTTP {exc.code}: {body}",
            )
        except (URLError, TimeoutError) as exc:
            return WebSearchResponse(
                ok=False,
                provider=self.name,
                query=request.query,
                error_code="WEB_SEARCH_PROVIDER_NETWORK_ERROR",
                message=f"Tavily 联网搜索网络失败：{exc}",
            )
        except Exception as exc:
            return WebSearchResponse(
                ok=False,
                provider=self.name,
                query=request.query,
                error_code="WEB_SEARCH_PROVIDER_FAILED",
                message=f"Tavily 联网搜索失败：{exc}",
            )

        results = _extract_results(payload)
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        answer = payload.get("answer") if isinstance(payload.get("answer"), str) else ""
        return WebSearchResponse(
            ok=True,
            provider=self.name,
            query=str(payload.get("query") or request.query),
            results=results[: request.max_results],
            conclusion=answer.strip(),
            raw_usage=usage,
            message=f"Tavily 联网搜索返回 {len(results[: request.max_results])} 条来源。",
        )

    def _call_tavily(self, request: WebSearchRequest) -> dict[str, Any]:
        body = _build_request_body(request)
        req = urlrequest.Request(
            self.endpoint,
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urlrequest.urlopen(req, timeout=self.timeout_seconds) as response:
            raw = response.read().decode("utf-8", errors="replace")
        parsed = json.loads(raw)
        return parsed if isinstance(parsed, dict) else {}


def _build_request_body(request: WebSearchRequest) -> dict[str, Any]:
    search_depth = (request.search_strategy or settings.web_search_strategy or "basic").strip().lower()
    if search_depth not in {"basic", "fast", "ultra-fast", "advanced"}:
        search_depth = "basic"
    body: dict[str, Any] = {
        "query": _build_query(request),
        "search_depth": search_depth,
        "max_results": max(1, min(int(request.max_results or settings.web_search_max_results), 10)),
        "topic": "general",
        "include_answer": "basic",
        "include_raw_content": False,
        "include_usage": True,
        "auto_parameters": False,
    }
    time_range = _time_range(request.freshness)
    if time_range:
        body["time_range"] = time_range
    include_domains = _include_domains(request)
    if include_domains:
        body["include_domains"] = include_domains
    if settings.web_search_blocked_domains:
        body["exclude_domains"] = settings.web_search_blocked_domains[:150]
    country = _country_for_locale(request.locale)
    if country:
        body["country"] = country
    return body


def _build_query(request: WebSearchRequest) -> str:
    query = request.query.strip()
    if request.site and f"site:{request.site}" not in query:
        return f"{query} site:{request.site.strip()}"
    return query


def _include_domains(request: WebSearchRequest) -> list[str]:
    domains: list[str] = []
    if request.site:
        domains.append(request.site.strip().lower())
    domains.extend(settings.web_search_allowed_domains)
    seen: set[str] = set()
    normalized: list[str] = []
    for domain in domains:
        clean = domain.removeprefix("http://").removeprefix("https://").split("/", 1)[0].strip().lower()
        if clean and clean not in seen:
            seen.add(clean)
            normalized.append(clean)
    return normalized[:300]


def _time_range(freshness: str) -> str:
    mapping = {
        "day": "day",
        "week": "week",
        "month": "month",
        "year": "year",
    }
    return mapping.get((freshness or "").strip().lower(), "")


def _country_for_locale(locale: str) -> str:
    normalized = (locale or "").strip().lower()
    if normalized.startswith("zh"):
        return "china"
    if normalized in {"en-us", "us"}:
        return "united states"
    return ""


def _extract_results(payload: dict[str, Any]) -> list[WebSearchResultItem]:
    candidates = payload.get("results")
    if not isinstance(candidates, list):
        return []
    results: list[WebSearchResultItem] = []
    for index, item in enumerate(candidates):
        result = _result_item_from_any(item, index + 1)
        if result is not None:
            results.append(result)
    return results


def _result_item_from_any(value: Any, rank: int) -> WebSearchResultItem | None:
    if not isinstance(value, dict):
        return None
    url = str(value.get("url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return None
    title = str(value.get("title") or url).strip()
    snippet = str(value.get("content") or value.get("snippet") or "").strip()
    published_at = value.get("published_date") or value.get("published_at")
    return WebSearchResultItem(
        title=title,
        url=url,
        snippet=snippet,
        source_domain=urlparse(url).netloc.lower(),
        published_at=published_at if isinstance(published_at, str) else None,
        rank=rank,
    )
