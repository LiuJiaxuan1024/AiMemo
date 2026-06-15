from __future__ import annotations

import json

from app.providers.web_search.factory import create_web_search_provider
from app.providers.web_search.tavily import TavilyWebSearchProvider
from app.schemas.web_search import WebSearchRequest


class _FakeHTTPResponse:
    def __init__(self, payload: dict):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


def test_tavily_provider_requires_api_key():
    provider = TavilyWebSearchProvider(api_key="")

    response = provider.search(WebSearchRequest(query="北京天气"))

    assert response.ok is False
    assert response.error_code == "WEB_SEARCH_KEY_MISSING"
    assert "TAVILY_API_KEY" in response.message


def test_tavily_provider_maps_search_response(monkeypatch):
    captured = {}

    def fake_urlopen(request, timeout):
        captured["timeout"] = timeout
        captured["headers"] = dict(request.header_items())
        captured["body"] = json.loads(request.data.decode("utf-8"))
        return _FakeHTTPResponse(
            {
                "query": "2026 政府工作报告 算电协同",
                "answer": "算电协同强调算力与电力协同布局。",
                "results": [
                    {
                        "title": "政府工作报告",
                        "url": "https://www.gov.cn/yaowen/liebiao/202603/content_123.htm",
                        "content": "报告提出推进算电协同。",
                        "score": 0.91,
                    }
                ],
                "usage": {"credits": 1},
            }
        )

    monkeypatch.setattr("app.providers.web_search.tavily.urlrequest.urlopen", fake_urlopen)
    provider = TavilyWebSearchProvider(api_key="tvly-test", timeout_seconds=7)

    response = provider.search(
        WebSearchRequest(
            query="2026 政府工作报告 算电协同",
            max_results=3,
            freshness="year",
            locale="zh-CN",
            search_strategy="basic",
        )
    )

    assert response.ok is True
    assert response.provider == "tavily"
    assert response.conclusion == "算电协同强调算力与电力协同布局。"
    assert response.raw_usage == {"credits": 1}
    assert response.results[0].title == "政府工作报告"
    assert response.results[0].source_domain == "www.gov.cn"
    assert captured["timeout"] == 7
    assert captured["headers"]["Authorization"] == "Bearer tvly-test"
    assert captured["body"]["search_depth"] == "basic"
    assert captured["body"]["time_range"] == "year"
    assert captured["body"]["country"] == "china"
    assert captured["body"]["include_answer"] == "basic"
    assert captured["body"]["auto_parameters"] is False


def test_web_search_factory_defaults_to_tavily(monkeypatch):
    monkeypatch.setattr("app.providers.web_search.factory.settings.web_search_provider", "")

    provider = create_web_search_provider()

    assert isinstance(provider, TavilyWebSearchProvider)
