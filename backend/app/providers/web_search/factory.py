from app.core.config import settings
from app.providers.web_search.aliyun_dashscope import AliyunDashScopeWebSearchProvider
from app.providers.web_search.provider import WebSearchProvider
from app.providers.web_search.tavily import TavilyWebSearchProvider


def create_web_search_provider(provider_name: str | None = None) -> WebSearchProvider:
    provider = (provider_name or settings.web_search_provider or "tavily").strip().lower()
    if provider == "tavily":
        return TavilyWebSearchProvider()
    if provider == "aliyun_dashscope":
        return AliyunDashScopeWebSearchProvider()
    raise ValueError(f"Unsupported web search provider: {provider}")
