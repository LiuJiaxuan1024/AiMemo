from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from html.parser import HTMLParser
import hashlib
import ipaddress
import json
import socket
from typing import Any
from urllib import request as urlrequest
from urllib.error import HTTPError, URLError
from urllib.parse import urlparse

from sqlmodel import Session, select

from app.core.config import settings
from app.models.note import utc_now
from app.models.web_search import WebSearchCache, WebSearchEvent, WebSearchUsage
from app.providers.web_search.factory import create_web_search_provider
from app.providers.web_search.provider import WebSearchProvider
from app.schemas.web_search import (
    WebFetchRequest,
    WebFetchResponse,
    WebSearchRequest,
    WebSearchResponse,
    WebSearchResultItem,
)


class WebSearchService:
    """Config, cache, quota and safety wrapper around web search providers."""

    def __init__(
        self,
        *,
        session: Session | None = None,
        provider: WebSearchProvider | None = None,
        conversation_id: int = 0,
    ) -> None:
        self.session = session
        self.provider = provider
        self.conversation_id = conversation_id

    def search(self, request: WebSearchRequest) -> WebSearchResponse:
        provider_name = request.provider or settings.web_search_provider
        request.provider = provider_name
        request.model = request.model or settings.web_search_model
        request.search_strategy = request.search_strategy or settings.web_search_strategy
        request.max_results = min(request.max_results or settings.web_search_max_results, settings.web_search_max_results)

        if not settings.web_search_enabled:
            response = WebSearchResponse(
                ok=False,
                provider=provider_name,
                query=request.query,
                error_code="WEB_SEARCH_DISABLED",
                message="联网搜索当前处于关闭状态。",
            )
            self._record_event("web_search", request.query, response)
            return response

        privacy_reason = classify_private_query(request.query)
        if settings.web_search_require_confirmation_for_private_queries and privacy_reason:
            response = WebSearchResponse(
                ok=False,
                provider=provider_name,
                query=request.query,
                error_code="WEB_SEARCH_PRIVATE_QUERY_CONFIRMATION_REQUIRED",
                message=f"搜索 query 可能包含隐私或项目敏感信息：{privacy_reason}",
            )
            self._record_event("web_search", request.query, response)
            return response

        cached = self._load_cache(request)
        if cached is not None:
            cached.cached = True
            self._record_event("web_search", request.query, cached)
            return cached

        if self._usage_exceeded(provider_name):
            response = WebSearchResponse(
                ok=False,
                provider=provider_name,
                query=request.query,
                error_code="WEB_SEARCH_DAILY_LIMIT_EXCEEDED",
                message="联网搜索今日额度已用尽。",
            )
            self._record_event("web_search", request.query, response)
            return response

        provider = self.provider or create_web_search_provider(provider_name)
        response = provider.search(request)
        if response.ok:
            self._increment_usage(provider_name)
            self._save_cache(request, response)
        self._record_event("web_search", request.query, response)
        return response

    def fetch(self, request: WebFetchRequest) -> WebFetchResponse:
        blocked_reason = validate_fetch_url(request.url)
        if blocked_reason:
            return WebFetchResponse(
                ok=False,
                url=request.url,
                error_code="WEB_FETCH_BLOCKED_URL",
                message=blocked_reason,
            )
        try:
            req = urlrequest.Request(
                request.url,
                headers={
                    "User-Agent": "AiMemo-WebFetch/1.0",
                    "Accept": "text/html,text/plain,application/json;q=0.9,*/*;q=0.1",
                },
                method="GET",
            )
            with urlrequest.urlopen(req, timeout=settings.web_search_fetch_timeout_seconds) as response:
                final_url = response.geturl()
                redirected_reason = validate_fetch_url(final_url)
                if redirected_reason:
                    return WebFetchResponse(
                        ok=False,
                        url=final_url,
                        error_code="WEB_FETCH_BLOCKED_URL",
                        message=f"重定向目标被阻止：{redirected_reason}",
                    )
                content_type = str(response.headers.get("Content-Type") or "").split(";", 1)[0].lower()
                if content_type and not _is_supported_content_type(content_type):
                    return WebFetchResponse(
                        ok=False,
                        url=final_url,
                        content_type=content_type,
                        error_code="WEB_FETCH_UNSUPPORTED_CONTENT_TYPE",
                        message=f"不支持抓取该内容类型：{content_type}",
                    )
                raw = response.read(max(request.max_chars * 4, 4096) + 1)
        except HTTPError as exc:
            return WebFetchResponse(
                ok=False,
                url=request.url,
                error_code="WEB_FETCH_HTTP_ERROR",
                message=f"网页抓取 HTTP {exc.code}",
            )
        except (URLError, TimeoutError) as exc:
            return WebFetchResponse(
                ok=False,
                url=request.url,
                error_code="WEB_FETCH_TIMEOUT",
                message=f"网页抓取失败：{exc}",
            )

        decoded = raw.decode("utf-8", errors="replace")
        title = ""
        text = decoded
        if "html" in content_type or "<html" in decoded[:500].lower():
            parser = _ReadableHTMLParser()
            parser.feed(decoded)
            title = parser.title.strip()
            text = parser.text()
        truncated = len(text) > request.max_chars
        if truncated:
            text = text[: request.max_chars].rstrip() + "..."
        return WebFetchResponse(
            ok=True,
            url=request.url,
            title=title,
            text=text.strip(),
            content_type=content_type,
            fetched_at=utc_now().isoformat(),
            truncated=truncated,
            message="网页抓取完成。",
        )

    def search_and_fetch(self, request: WebSearchRequest) -> WebSearchResponse:
        response = self.search(request)
        if not response.ok:
            return response
        verified: list[WebSearchResultItem] = []
        for item in response.results:
            if len([candidate for candidate in verified if candidate.fetched]) >= settings.web_search_fetch_verify_max_results:
                verified.append(item)
                continue
            if not _domain_allowed(item.url):
                verified.append(item)
                continue
            fetch_response = self.fetch(WebFetchRequest(url=item.url, max_chars=4000))
            if fetch_response.ok:
                item.fetched = True
                item.fetch_title = fetch_response.title
                item.fetch_text_preview = fetch_response.text[:1200]
            verified.append(item)
        response.results = verified
        return response

    def _load_cache(self, request: WebSearchRequest) -> WebSearchResponse | None:
        if self.session is None or settings.web_search_cache_ttl_seconds <= 0:
            return None
        query_hash = _query_hash(request.query)
        row = self.session.exec(
            select(WebSearchCache).where(
                WebSearchCache.provider == request.provider,
                WebSearchCache.query_hash == query_hash,
                WebSearchCache.locale == request.locale,
                WebSearchCache.freshness == request.freshness,
                WebSearchCache.site == request.site,
                WebSearchCache.expires_at > utc_now(),
            )
        ).first()
        if row is None:
            return None
        try:
            return WebSearchResponse.model_validate_json(row.results_json)
        except Exception:
            return None

    def _save_cache(self, request: WebSearchRequest, response: WebSearchResponse) -> None:
        if self.session is None or settings.web_search_cache_ttl_seconds <= 0:
            return
        now = utc_now()
        query_hash = _query_hash(request.query)
        row = self.session.exec(
            select(WebSearchCache).where(
                WebSearchCache.provider == request.provider,
                WebSearchCache.query_hash == query_hash,
                WebSearchCache.locale == request.locale,
                WebSearchCache.freshness == request.freshness,
                WebSearchCache.site == request.site,
            )
        ).first()
        if row is None:
            row = WebSearchCache(
                provider=request.provider,
                query_hash=query_hash,
                query_text=request.query[:1000],
                locale=request.locale,
                freshness=request.freshness,
                site=request.site,
                created_at=now,
                expires_at=now,
            )
            self.session.add(row)
        row.results_json = response.model_dump_json()
        row.expires_at = now + timedelta(seconds=settings.web_search_cache_ttl_seconds)
        self.session.commit()

    def _usage_exceeded(self, provider: str) -> bool:
        if self.session is None or settings.web_search_daily_limit <= 0:
            return False
        usage = self.session.exec(
            select(WebSearchUsage).where(
                WebSearchUsage.provider == provider,
                WebSearchUsage.usage_date == date.today(),
            )
        ).first()
        return bool(usage and usage.request_count >= settings.web_search_daily_limit)

    def _increment_usage(self, provider: str) -> None:
        if self.session is None or settings.web_search_daily_limit <= 0:
            return
        today = date.today()
        usage = self.session.exec(
            select(WebSearchUsage).where(
                WebSearchUsage.provider == provider,
                WebSearchUsage.usage_date == today,
            )
        ).first()
        if usage is None:
            usage = WebSearchUsage(provider=provider, usage_date=today, request_count=0)
            self.session.add(usage)
        usage.request_count += 1
        usage.updated_at = utc_now()
        self.session.commit()

    def _record_event(self, tool_name: str, query: str, response: WebSearchResponse) -> None:
        if self.session is None:
            return
        self.session.add(
            WebSearchEvent(
                conversation_id=self.conversation_id,
                provider=response.provider,
                tool_name=tool_name,
                query_hash=_query_hash(query),
                query_preview=query[:240],
                result_count=len(response.results),
                cached=response.cached,
                error_code=response.error_code,
            )
        )
        self.session.commit()


def classify_private_query(query: str) -> str:
    text = query.strip()
    if not text:
        return ""
    patterns = [
        (r"(?i)(api[_-]?key|secret|token|password|passwd|access_key|ak/sk)", "可能包含密钥或凭据"),
        (r"(?i)(/home/[^/\s]+|[A-Za-z]:\\|\.env|id_rsa|ssh)", "可能包含本地路径或敏感文件名"),
        (r"\b\d{15,18}[\dxX]?\b", "可能包含身份证号"),
        (r"\b1[3-9]\d{9}\b", "可能包含手机号"),
    ]
    for pattern, reason in patterns:
        if re_search(pattern, text):
            return reason
    if len(text) > 800:
        return "query 过长，可能直接包含私人笔记原文"
    return ""


def validate_fetch_url(url: str) -> str:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return "只允许抓取 http(s) URL。"
    hostname = parsed.hostname
    if not hostname:
        return "URL 缺少 hostname。"
    if hostname.lower() in {"localhost"}:
        return "禁止抓取 localhost。"
    try:
        addresses = socket.getaddrinfo(hostname, None)
    except socket.gaierror as exc:
        return f"无法解析域名：{exc}"
    for address in addresses:
        ip = ipaddress.ip_address(address[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            return "禁止抓取内网、回环或保留地址。"
    return ""


def _domain_allowed(url: str) -> bool:
    domain = urlparse(url).netloc.lower()
    if any(domain == blocked or domain.endswith("." + blocked) for blocked in settings.web_search_blocked_domains):
        return False
    allowed = settings.web_search_allowed_domains
    if allowed and not any(domain == item or domain.endswith("." + item) for item in allowed):
        return False
    return True


def _is_supported_content_type(content_type: str) -> bool:
    return content_type in {
        "text/html",
        "text/plain",
        "application/json",
        "application/xhtml+xml",
        "",
    }


def _query_hash(query: str) -> str:
    return hashlib.sha256(query.strip().encode("utf-8")).hexdigest()


def re_search(pattern: str, text: str) -> bool:
    import re

    return re.search(pattern, text) is not None


class _ReadableHTMLParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._in_title = False
        self._skip_depth = 0
        self.title = ""
        self._parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.lower()
        if lowered == "title":
            self._in_title = True
        if lowered in {"script", "style", "noscript", "svg"}:
            self._skip_depth += 1

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered == "title":
            self._in_title = False
        if lowered in {"script", "style", "noscript", "svg"} and self._skip_depth > 0:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        text = " ".join(data.split())
        if not text:
            return
        if self._in_title:
            self.title += text
        if self._skip_depth == 0:
            self._parts.append(text)

    def text(self) -> str:
        return "\n".join(self._parts)
