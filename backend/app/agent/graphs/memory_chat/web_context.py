from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
import logging
import re
from typing import Literal

from sqlmodel import Session

from app.agent.context import ContextBudget, ContextLayer
from app.agent.graphs.memory_chat.state import MemoryChatGraphState
from app.core.config import settings
from app.core.timing import elapsed_ms
from app.rag.chunking.tokenizer import count_tokens
from app.schemas.web_search import WebSearchRequest
from app.services.web_search_service import WebSearchService, classify_private_query


SessionFactory = Callable[[], AbstractContextManager[Session]]
logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class WebSearchPlan:
    """Lx.web 联网搜索规划结果。"""

    action: Literal["skip", "search", "confirm"]
    query: str
    freshness: Literal["any", "day", "week", "month", "year"]
    site: str
    reason: str
    privacy_risk: str = ""
    source: str = "rule"


def _context_budget() -> ContextBudget:
    return settings.context_pyramid_budget


def _resolve_user_message(state: MemoryChatGraphState) -> str:
    user_message = state.get("user_message", "").strip()
    if not user_message:
        raise ValueError("user_message is required.")
    return user_message


def _resolve_conversation_id(state: MemoryChatGraphState) -> int:
    conversation_id = state.get("conversation_id")
    if conversation_id is None:
        raise ValueError("conversation_id is required.")
    return int(conversation_id)


def _indent_text(text: str, spaces: int) -> str:
    prefix = " " * spaces
    return "\n".join(f"{prefix}{line}" if line else line for line in text.splitlines())


def _truncate_context_text(text: str, budget_tokens: int) -> str:
    normalized = text.strip()
    if count_tokens(normalized) <= budget_tokens:
        return normalized
    candidate = normalized[: max(1, budget_tokens * 2)]
    while candidate and count_tokens(candidate + "...") > budget_tokens:
        candidate = candidate[: max(1, int(len(candidate) * 0.85))]
    return candidate.rstrip() + "..."




def build_lx_web_context_node(session_factory: SessionFactory):
    """构建 Lx.web 联网搜索上下文层。"""

    def build_lx_web_context(state: MemoryChatGraphState) -> MemoryChatGraphState:
        user_message = _resolve_user_message(state)
        plan = plan_lx_web_context(state)
        budget_tokens = min(_context_budget().summary_tokens, 4000)
        debug = {
            "action": plan.action,
            "query": plan.query,
            "freshness": plan.freshness,
            "site": plan.site,
            "reason": plan.reason,
            "privacy_risk": plan.privacy_risk,
            "provider": settings.web_search_provider,
        }

        if plan.action == "skip":
            content = f"本轮未执行联网搜索。\n原因：{plan.reason}"
            layer = ContextLayer(
                level=0,
                name="联网搜索上下文（Lx.web）",
                content=content,
                budget_tokens=budget_tokens,
                used_tokens=count_tokens(content),
                note="没有成功联网搜索 observation 时，不要声称已经查询公网。",
            )
            return {
                "context_lx_web_layer": layer.to_payload(),
                "web_search_debug": debug,
            }

        if plan.action == "confirm":
            content = (
                "本轮没有自动联网搜索，因为搜索 query 可能包含隐私或项目敏感信息。\n"
                f"建议 query：{plan.query or user_message}\n"
                f"风险：{plan.privacy_risk or '需要用户确认后才能外发 query'}"
            )
            layer = ContextLayer(
                level=0,
                name="联网搜索上下文（Lx.web）",
                content=content,
                budget_tokens=budget_tokens,
                used_tokens=count_tokens(content),
                note="如确需联网，应先调用 request_user_input 确认是否允许外发该 query。",
            )
            return {
                "context_lx_web_layer": layer.to_payload(),
                "web_search_debug": debug,
            }

        with session_factory() as session:
            service = WebSearchService(session=session, conversation_id=_resolve_conversation_id(state))
            response = service.search_and_fetch(
                WebSearchRequest(
                    query=plan.query,
                    max_results=settings.web_search_max_results,
                    freshness=plan.freshness,
                    locale="zh-CN",
                    site=plan.site,
                    provider=settings.web_search_provider,
                    model=settings.web_search_model,
                    search_strategy=settings.web_search_strategy,
                )
            )
        debug.update(
            {
                "ok": response.ok,
                "error_code": response.error_code,
                "message": response.message,
                "result_count": len(response.results),
                "cached": response.cached,
            }
        )
        layer = _build_lx_web_context_layer(plan, response, budget_tokens=budget_tokens)
        return {
            "context_lx_web_layer": layer.to_payload(),
            "web_search_debug": debug,
        }

    return build_lx_web_context


def plan_lx_web_context(state: MemoryChatGraphState) -> WebSearchPlan:
    """Plan whether this turn needs public web evidence."""

    user_message = _resolve_user_message(state).strip()
    if not settings.web_search_enabled:
        return WebSearchPlan(
            action="skip",
            query="",
            freshness="any",
            site="",
            reason="web_search.enabled=false。",
        )
    if not user_message:
        return WebSearchPlan(action="skip", query="", freshness="any", site="", reason="当前输入为空。")
    local_skip_reason = _local_context_question_reason(user_message)
    if local_skip_reason:
        return WebSearchPlan(action="skip", query="", freshness="any", site="", reason=local_skip_reason)

    should_search, reason = _should_search_public_web(user_message)
    if not should_search:
        return WebSearchPlan(action="skip", query="", freshness="any", site="", reason=reason)

    query = _minimize_web_search_query(user_message)
    privacy_risk = classify_private_query(query)
    if privacy_risk:
        return WebSearchPlan(
            action="confirm",
            query=query,
            freshness=_infer_web_search_freshness(user_message),
            site=_infer_web_search_site(user_message),
            reason="query 可能包含隐私，需先确认。",
            privacy_risk=privacy_risk,
        )
    return WebSearchPlan(
        action="search",
        query=query,
        freshness=_infer_web_search_freshness(user_message),
        site=_infer_web_search_site(user_message),
        reason=reason,
    )


def _build_lx_web_context_layer(
    plan: WebSearchPlan,
    response,
    *,
    budget_tokens: int,
) -> ContextLayer:
    if not response.ok:
        content = (
            f"联网搜索未成功。\nquery: {plan.query}\n"
            f"error_code: {response.error_code}\nmessage: {response.message}"
        )
        note = "联网搜索失败；回答时必须说明未能联网，不要声称已查到最新网页。"
    elif not response.results:
        content = f"联网搜索已执行，但没有返回可用来源。\nquery: {plan.query}\nmessage: {response.message}"
        note = "没有可引用 URL；回答不能把搜索摘要当成可靠来源。"
    else:
        lines = [
            f"query: {plan.query}",
            f"provider: {response.provider}",
            f"cached: {str(response.cached).lower()}",
            f"reason: {plan.reason}",
        ]
        if response.conclusion:
            lines.append(f"provider_conclusion: {response.conclusion}")
        lines.append("sources:")
        for index, item in enumerate(response.results, start=1):
            lines.extend(
                [
                    f"- [{index}] {item.title or item.url}",
                    f"  url: {item.url}",
                    f"  domain: {item.source_domain}",
                    f"  fetched: {str(item.fetched).lower()}",
                ]
            )
            if item.snippet:
                lines.append(f"  snippet: {item.snippet}")
            if item.fetch_title:
                lines.append(f"  fetched_title: {item.fetch_title}")
            if item.fetch_text_preview:
                lines.append("  fetched_preview:\n" + _indent_text(item.fetch_text_preview, 4))
        content = _truncate_context_text("\n".join(lines), budget_tokens)
        note = "本层来自公网搜索。使用其中事实回答时必须列出来源 URL；价格、政策、API 参数优先信任 fetched=true 的官方来源。"
    return ContextLayer(
        level=0,
        name="联网搜索上下文（Lx.web）",
        content=content,
        budget_tokens=budget_tokens,
        used_tokens=count_tokens(content),
        note=note,
    )


def _local_context_question_reason(text: str) -> str:
    lowered = text.lower()
    local_markers = [
        "我之前",
        "我刚才",
        "我的笔记",
        "本地",
        "这个项目",
        "当前项目",
        "仓库",
        "代码",
        "文件",
        "目录",
        "知识库",
        "挂载",
        "previous note",
        "local file",
        "this repo",
    ]
    if any(marker in lowered for marker in local_markers):
        freshness_markers = ["最新", "官网", "联网", "网上", "当前价格", "计费", "release", "changelog"]
        if not any(marker in lowered for marker in freshness_markers):
            return "问题更像是在问本地/个人/挂载上下文，优先使用已有上下文，不主动联网。"
    return ""


def _should_search_public_web(text: str) -> tuple[bool, str]:
    lowered = text.lower()
    if re.search(r"20\d{2}\s*年", text) and any(
        marker in text for marker in ["政府工作报告", "政策", "报告", "提出", "概念"]
    ):
        return True, "当前问题包含明确年份和公共政策/报告概念，适合联网核验来源。"
    triggers = [
        "联网",
        "网上",
        "搜索",
        "搜一下",
        "查一下",
        "查官网",
        "官网",
        "政府工作报告",
        "最新",
        "当前",
        "现在",
        "今天",
        "今年",
        "价格",
        "计费",
        "限额",
        "版本",
        "发布",
        "新闻",
        "法规",
        "政策",
        "标准",
        "文档",
        "source",
        "citation",
        "official",
        "latest",
        "current",
        "price",
        "pricing",
        "release",
        "changelog",
    ]
    if any(trigger in lowered for trigger in triggers):
        return True, "当前问题包含公网搜索或时效信息触发词。"
    return False, "未检测到明确公网时效信息需求。"


def _minimize_web_search_query(text: str) -> str:
    normalized = re.sub(r"\s+", " ", text).strip()
    normalized = re.sub(r"(?i)(请|帮我|能不能|可以|麻烦|联网|网上|搜一下|搜索一下|查一下)", " ", normalized)
    normalized = re.sub(r"\s+", " ", normalized).strip(" ，。?？")
    if len(normalized) > 160:
        normalized = normalized[:160].rstrip()
    return normalized or text.strip()[:160]


def _infer_web_search_freshness(text: str) -> Literal["any", "day", "week", "month", "year"]:
    lowered = text.lower()
    if any(marker in lowered for marker in ["今天", "今日", "today"]):
        return "day"
    if any(marker in lowered for marker in ["本周", "最近", "latest", "recent"]):
        return "week"
    if any(marker in lowered for marker in ["本月", "当前", "现在", "价格", "计费", "current", "pricing"]):
        return "month"
    if any(marker in lowered for marker in ["今年", "2026", "year"]):
        return "year"
    return "any"


def _infer_web_search_site(text: str) -> str:
    lowered = text.lower()
    if "阿里" in text or "aliyun" in lowered or "dashscope" in lowered or "百炼" in text:
        return "aliyun.com"
    if "openai" in lowered:
        return "openai.com"
    return ""
