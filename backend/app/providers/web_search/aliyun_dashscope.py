from __future__ import annotations

import json
import os
import re
from typing import Any
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

from app.ai.json_utils import parse_json_object
from app.core.config import settings
from app.schemas.web_search import WebSearchRequest, WebSearchResponse, WebSearchResultItem


class AliyunDashScopeWebSearchProvider:
    """DashScope search-augmented generation provider.

    DashScope is not modeled as a raw SERP API here. It is a model call with
    search enabled; we normalize either `search_info` results or model-emitted
    JSON sources into AiMemo's web search response shape.
    """

    name = "aliyun_dashscope"

    def __init__(self, *, api_key: str | None = None, timeout_seconds: int | None = None) -> None:
        self.api_key = api_key if api_key is not None else settings.dashscope_api_key or os.getenv("DASHSCOPE_API_KEY", "")
        self.timeout_seconds = timeout_seconds or settings.web_search_timeout_seconds

    def search(self, request: WebSearchRequest) -> WebSearchResponse:
        if not self.api_key:
            return WebSearchResponse(
                ok=False,
                provider=self.name,
                query=request.query,
                error_code="WEB_SEARCH_KEY_MISSING",
                message="缺少 DASHSCOPE_API_KEY，无法使用阿里联网搜索。",
            )
        try:
            payload = self._call_dashscope(request)
        except HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")[:1000]
            return WebSearchResponse(
                ok=False,
                provider=self.name,
                query=request.query,
                error_code="WEB_SEARCH_PROVIDER_HTTP_ERROR",
                message=f"DashScope 联网搜索 HTTP {exc.code}: {body}",
            )
        except (URLError, TimeoutError) as exc:
            return WebSearchResponse(
                ok=False,
                provider=self.name,
                query=request.query,
                error_code="WEB_SEARCH_PROVIDER_NETWORK_ERROR",
                message=f"DashScope 联网搜索网络失败：{exc}",
            )
        except Exception as exc:
            return WebSearchResponse(
                ok=False,
                provider=self.name,
                query=request.query,
                error_code="WEB_SEARCH_PROVIDER_FAILED",
                message=f"DashScope 联网搜索失败：{exc}",
            )

        content = _extract_message_content(payload)
        results = _extract_search_results(payload)
        if not results:
            results = _extract_sources_from_content(content)
        usage = payload.get("usage") if isinstance(payload.get("usage"), dict) else {}
        return WebSearchResponse(
            ok=True,
            provider=self.name,
            query=request.query,
            results=results[: request.max_results],
            conclusion=_extract_conclusion(content),
            raw_usage=usage,
            message=f"DashScope 联网搜索返回 {len(results[: request.max_results])} 条来源。",
        )

    def _call_dashscope(self, request: WebSearchRequest) -> dict[str, Any]:
        prompt = (
            "请联网搜索并回答用户查询。必须优先给出官方或高可信来源。"
            "输出尽量使用 JSON：{\"conclusion\":\"...\",\"sources\":[{\"title\":\"...\",\"url\":\"...\",\"snippet\":\"...\"}]}。"
            "不要编造 URL。"
        )
        body = {
            "model": request.model or settings.web_search_model,
            "input": {
                "messages": [
                    {"role": "system", "content": prompt},
                    {"role": "user", "content": _build_search_query_text(request)},
                ]
            },
            "parameters": {
                "enable_search": True,
                "result_format": "message",
                "temperature": 0.1,
                "search_options": {
                    "search_strategy": request.search_strategy or settings.web_search_strategy,
                },
            },
        }
        req = urlrequest.Request(
            "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation",
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


def _build_search_query_text(request: WebSearchRequest) -> str:
    parts = [request.query.strip()]
    if request.site:
        parts.append(f"site:{request.site.strip()}")
    if request.freshness and request.freshness != "any":
        parts.append(f"freshness:{request.freshness}")
    if request.locale:
        parts.append(f"locale:{request.locale}")
    return "\n".join(parts)


def _extract_message_content(payload: dict[str, Any]) -> str:
    output = payload.get("output")
    if isinstance(output, dict):
        choices = output.get("choices")
        if isinstance(choices, list) and choices:
            message = choices[0].get("message") if isinstance(choices[0], dict) else None
            if isinstance(message, dict):
                content = message.get("content")
                if isinstance(content, str):
                    return content.strip()
    return ""


def _extract_search_results(payload: dict[str, Any]) -> list[WebSearchResultItem]:
    for node in _walk_dicts(payload):
        candidates = node.get("search_results") or node.get("results")
        if not isinstance(candidates, list):
            continue
        items = [_result_item_from_any(item, index + 1) for index, item in enumerate(candidates)]
        items = [item for item in items if item is not None]
        if items:
            return items
    return []


def _walk_dicts(value: Any):
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from _walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_dicts(child)


def _result_item_from_any(value: Any, rank: int) -> WebSearchResultItem | None:
    if not isinstance(value, dict):
        return None
    url = str(value.get("url") or value.get("link") or value.get("source_url") or "").strip()
    if not url.startswith(("http://", "https://")):
        return None
    title = str(value.get("title") or value.get("name") or url).strip()
    snippet = str(value.get("snippet") or value.get("summary") or value.get("content") or "").strip()
    return WebSearchResultItem(
        title=title,
        url=url,
        snippet=snippet,
        source_domain=urlparse(url).netloc.lower(),
        published_at=value.get("published_at") if isinstance(value.get("published_at"), str) else None,
        rank=rank,
    )


def _extract_sources_from_content(content: str) -> list[WebSearchResultItem]:
    if not content:
        return []
    parsed = parse_json_object(content)
    sources = parsed.get("sources") if isinstance(parsed, dict) else None
    if isinstance(sources, list):
        items = [_result_item_from_any(item, index + 1) for index, item in enumerate(sources)]
        items = [item for item in items if item is not None]
        if items:
            return items

    urls = []
    seen = set()
    for match in re.finditer(r"https?://[^\s\]\)\"'<>，。；]+", content):
        url = match.group(0).rstrip(".,;:")
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
    return [
        WebSearchResultItem(
            title=urlparse(url).netloc.lower() or url,
            url=url,
            source_domain=urlparse(url).netloc.lower(),
            rank=index + 1,
        )
        for index, url in enumerate(urls)
    ]


def _extract_conclusion(content: str) -> str:
    if not content:
        return ""
    parsed = parse_json_object(content)
    if isinstance(parsed.get("conclusion"), str):
        return str(parsed.get("conclusion")).strip()
    return content.strip()[:1200]
