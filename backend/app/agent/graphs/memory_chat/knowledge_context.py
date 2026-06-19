from collections.abc import Callable
from contextlib import AbstractContextManager
import logging
import re

from sqlmodel import Session, col, select

from app.agent.context import ContextBudget
from app.agent.graphs.memory_chat.state import (
    KnowledgeRetrievedChunkPayload,
    MemoryChatGraphState,
    MountedKnowledgeSpacePayload,
)
from app.core.config import settings
from app.core.timing import elapsed_ms, now_counter
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument, KnowledgeSpace
from app.rag.chunking.tokenizer import count_tokens
from app.services.knowledge_mount_service import list_conversation_knowledge_mounts
from app.services.knowledge_search_service import KnowledgeSearchItem, search_mounted_knowledge


SessionFactory = Callable[[], AbstractContextManager[Session]]
logger = logging.getLogger(__name__)


def _search_mounted_knowledge(*args, **kwargs):
    from app.agent.graphs.memory_chat import nodes as nodes_facade

    return nodes_facade.search_mounted_knowledge(*args, **kwargs)

KNOWLEDGE_RETRIEVAL_TRIGGERS = [
    "知识库",
    "知识空间",
    "挂载",
    "文档",
    "资料",
    "文件",
    "项目资料",
    "根据",
    "基于",
    "查一下",
    "查找",
    "搜索",
    "检索",
    "引用",
    "出处",
    "来源",
    "总结",
    "概括",
    "分析",
    "对比",
    "说明",
    "里面",
    "这份",
    "这篇",
    "this document",
    "knowledge",
    "document",
    "docs",
    "file",
    "search",
    "according to",
]

KNOWLEDGE_RETRIEVAL_PROFILES = {
    "focused": {"top_k": 5, "per_document_limit": 3},
    "expanded": {"top_k": 10, "per_document_limit": 6},
    "deep": {"top_k": 20, "per_document_limit": 9},
}


def _context_budget() -> ContextBudget:
    return settings.context_pyramid_budget


def _resolve_conversation_id(state: MemoryChatGraphState) -> int:
    conversation_id = state.get("conversation_id")
    if conversation_id is None:
        raise ValueError("conversation_id is required.")
    return int(conversation_id)


def _resolve_user_message(state: MemoryChatGraphState) -> str:
    user_message = state.get("user_message", "").strip()
    if not user_message:
        raise ValueError("user_message is required.")
    return user_message


def build_l3_knowledge_context_node(
    session_factory: SessionFactory,
    *,
    limit: int = 5,
):
    """构建 L3.5 会话挂载知库层。

    这层只读取当前 conversation 显式挂载的知识空间。没有挂载时不检索，
    避免 Agent 越过用户的二重防护边界去全局搜索知识库。
    """

    def build_l3_knowledge_context(state: MemoryChatGraphState) -> MemoryChatGraphState:
        conversation_id = _resolve_conversation_id(state)
        user_message = _resolve_user_message(state)
        started_at = now_counter()
        debug: dict = {
            "status": "skipped",
            "mounted_count": 0,
            "needs_retrieval": False,
            "retrieved_count": 0,
            "query": "",
        }
        mounted_spaces: list[MountedKnowledgeSpacePayload] = []
        retrieved_chunks: list[KnowledgeRetrievedChunkPayload] = []
        recall_cache: list[KnowledgeRetrievedChunkPayload] = []
        needs_retrieval = False
        reason = "当前对话未挂载知识空间。"
        retrieval_query = ""

        try:
            with session_factory() as session:
                mounts = list_conversation_knowledge_mounts(session, conversation_id)
                mounted_spaces = [
                    {
                        "space_id": mount.space_id,
                        "space_name": mount.space_name,
                        "space_icon": mount.space_icon,
                        "ready_document_count": mount.ready_document_count,
                        "document_count": mount.document_count,
                    }
                    for mount in mounts
                ]
                debug["mounted_count"] = len(mounted_spaces)
                debug["mounted_spaces"] = [
                    {"space_id": item["space_id"], "space_name": item["space_name"]}
                    for item in mounted_spaces
                ]

                if mounted_spaces:
                    needs_retrieval, reason = _should_retrieve_mounted_knowledge(user_message, mounted_spaces)
                    debug["needs_retrieval"] = needs_retrieval
                    retrieval_query = user_message.strip() if needs_retrieval else ""
                    debug["query"] = retrieval_query
                    if needs_retrieval:
                        search_result = _search_mounted_knowledge(
                            session,
                            conversation_id=conversation_id,
                            query=retrieval_query,
                            top_k=limit,
                            mode="hybrid",
                        )
                        debug["status"] = search_result.status
                        retrieved_chunks = [
                            _to_knowledge_chunk_payload(item)
                            for item in search_result.results
                        ]
                        recall_cache = [
                            _to_knowledge_chunk_payload(item)
                            for item in search_result.recall_cache
                        ]
                        debug["retrieved_count"] = len(retrieved_chunks)
                        debug["recall_cache_count"] = len(recall_cache)
                        debug["retrieval_profile"] = "focused"
                        debug["per_document_limit"] = search_result.per_document_limit
                    else:
                        debug["status"] = "not_needed"

            layer = _build_knowledge_context_layer(
                mounted_spaces,
                retrieved_chunks,
                needs_retrieval=needs_retrieval,
                reason=reason,
            )
            debug["total_ms"] = elapsed_ms(started_at)
        except Exception as exc:
            logger.exception("memory_chat.l3_knowledge_failed conversation_id=%s", conversation_id)
            debug.update(
                {
                    "status": "failed",
                    "degraded": True,
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "total_ms": elapsed_ms(started_at),
                }
            )
            reason = "挂载知库检索失败，已降级为不使用知库上下文。"
            needs_retrieval = False
            retrieval_query = ""
            retrieved_chunks = []
            recall_cache = []
            layer = _build_knowledge_context_layer(
                mounted_spaces,
                retrieved_chunks,
                needs_retrieval=False,
                reason=reason,
            )

        return {
            "mounted_knowledge_spaces": mounted_spaces,
            "needs_knowledge_retrieval": needs_retrieval,
            "knowledge_retrieval_query": retrieval_query,
            "knowledge_retrieval_reason": reason,
            "knowledge_retrieved_chunks": retrieved_chunks,
            "knowledge_recall_cache": recall_cache,
            "knowledge_retrieval_debug": debug,
            "context_l3_knowledge_layer": layer.to_payload(),
        }

    return build_l3_knowledge_context


def _should_retrieve_mounted_knowledge(
    user_message: str,
    mounted_spaces: list[MountedKnowledgeSpacePayload],
) -> tuple[bool, str]:
    text = user_message.strip()
    if not mounted_spaces:
        return False, "当前对话未挂载知识空间。"
    if not text:
        return False, "当前用户输入为空。"
    lowered = text.lower()
    if any(trigger.lower() in lowered for trigger in KNOWLEDGE_RETRIEVAL_TRIGGERS):
        return True, "用户问题显式指向文档/资料/知识库或需要基于外部资料回答。"
    if any(str(space.get("space_name") or "").strip() and str(space.get("space_name") or "").lower() in lowered for space in mounted_spaces):
        return True, "用户提到了已挂载知识空间名称。"
    if _is_clear_casual_or_common_fact_message(text):
        return False, "当前对话已挂载知识空间，但本轮是明确闲聊或客观常识，跳过知库检索。"
    return True, "当前对话已挂载知识空间，默认先检索挂载资料以避免遗漏上下文。"


def _looks_like_knowledge_question(text: str) -> bool:
    question_markers = ["？", "?", "怎么", "如何", "为什么", "是否", "哪些", "什么", "帮我", "解释", "总结", "分析"]
    return any(marker in text for marker in question_markers)


def _is_clear_casual_or_common_fact_message(text: str) -> bool:
    normalized = re.sub(r"\s+", "", text.strip().lower())
    normalized = normalized.strip("。.!！?？~～")
    if not normalized:
        return True

    casual_messages = {
        "你好",
        "您好",
        "嗨",
        "hi",
        "hello",
        "晚上好",
        "早上好",
        "中午好",
        "下午好",
        "晚安",
        "谢谢",
        "谢谢你",
        "感谢",
        "辛苦了",
        "好的",
        "好",
        "嗯",
        "嗯嗯",
        "可以",
        "收到",
        "明白",
        "再见",
        "拜拜",
        "你是谁",
        "你叫什么",
        "你在吗",
    }
    if normalized in casual_messages:
        return True

    casual_patterns = [
        r"^(你)?在吗$",
        r"^你好吗$",
        r"^今天过得怎么样$",
        r"^最近怎么样$",
        r"^你能做什么$",
        r"^你会做什么$",
    ]
    if any(re.search(pattern, normalized) for pattern in casual_patterns):
        return True

    if re.fullmatch(r"[\d零一二三四五六七八九十百千万两\s+\-*/×÷().（）=＝]+(等于几|等于多少|等于|是多少|怎么算|几|吗)?", normalized):
        return True

    common_fact_patterns = [
        r"^水的化学式(是)?什么$",
        r"^太阳从哪边升起$",
        r"^太阳从东边升起吗$",
        r"^一周有几天$",
        r"^一年有多少天$",
        r"^北京是中国(的)?首都吗$",
        r"^中国(的)?首都(是)?哪里$",
    ]
    return any(re.search(pattern, normalized) for pattern in common_fact_patterns)


def _build_knowledge_context_layer(
    mounted_spaces: list[MountedKnowledgeSpacePayload],
    retrieved_chunks: list[KnowledgeRetrievedChunkPayload],
    *,
    needs_retrieval: bool,
    reason: str,
):
    from app.agent.context import ContextLayer

    budget = _context_budget()
    mount_summary = _format_mounted_knowledge_spaces(mounted_spaces)
    if not mounted_spaces:
        content = "当前对话未挂载知识空间。不能搜索或引用全局知识库；如用户需要基于文档回答，请先提示用户挂载知识空间。"
        note = "二重防护：未挂载即不可检索。"
    elif retrieved_chunks:
        chunk_lines = [
            _format_knowledge_chunk_for_prompt(chunk, index=index)
            for index, chunk in enumerate(retrieved_chunks, start=1)
        ]
        chunk_text = _fit_knowledge_lines_to_budget(chunk_lines, budget.retrieved_memory_tokens)
        content = f"{mount_summary}\n\n本轮检索原因：{reason}\n\n{chunk_text}"
        note = "仅包含当前会话已挂载知识空间的检索结果；[K] 编号只用于内部定位，最终回答不要裸露输出。"
    elif needs_retrieval:
        content = f"{mount_summary}\n\n本轮检索原因：{reason}\n检索结果：没有找到足够相关的挂载知识片段。"
        note = "只允许说明挂载范围内未检索到依据，不能扩展为全局知识库结论。"
    else:
        content = f"{mount_summary}\n\n本轮未检索挂载知识库。原因：{reason}"
        note = "已挂载时默认检索；仅在明确闲聊或客观常识问题中跳过。"

    return ContextLayer(
        level=3.5,
        name="挂载知识空间检索",
        content=content,
        budget_tokens=budget.retrieved_memory_tokens,
        used_tokens=count_tokens(content),
        note=note,
    )


def _fit_knowledge_lines_to_budget(lines: list[str], budget_tokens: int) -> str:
    selected: list[str] = []
    used_tokens = 0
    for line in lines:
        line_tokens = count_tokens(line)
        if selected and used_tokens + line_tokens > budget_tokens:
            break
        if not selected and line_tokens > budget_tokens:
            return line[: max(1, budget_tokens * 2)].rstrip() + "..."
        selected.append(line)
        used_tokens += line_tokens
    return "\n".join(selected) if selected else "无。"


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


def _format_mounted_knowledge_spaces(spaces: list[MountedKnowledgeSpacePayload]) -> str:
    if not spaces:
        return "已挂载知识空间：无。"
    lines = ["已挂载知识空间："]
    for space in spaces:
        lines.append(
            f"- {space.get('space_name')} "
            f"(space_id={space.get('space_id')}, ready_docs={space.get('ready_document_count')}/{space.get('document_count')})"
        )
    return "\n".join(lines)


def _format_knowledge_chunk_for_prompt(chunk: KnowledgeRetrievedChunkPayload, *, index: int) -> str:
    heading = " / ".join(chunk.get("heading_path") or [])
    heading_text = f" > {heading}" if heading else ""
    page = chunk.get("page_number")
    page_text = f", p.{page}" if page is not None else ""
    score = chunk.get("score")
    score_text = f", score={float(score):.3f}" if score is not None else ""
    source = chunk.get("document_title") or chunk.get("original_filename") or f"document:{chunk.get('document_id')}"
    text = str(chunk.get("text") or "").strip()
    return f"- [K{index}] {source}{heading_text}{page_text}{score_text}\n  {text}"


def _to_knowledge_chunk_payload(item: KnowledgeSearchItem) -> KnowledgeRetrievedChunkPayload:
    return {
        "chunk_id": item.chunk_id,
        "space_id": item.space_id,
        "space_name": item.space_name,
        "document_id": item.document_id,
        "document_title": item.document_title,
        "text": item.text,
        "score": item.score,
        "score_source": item.score_source,
        "heading_path": item.heading_path,
        "page_number": item.page_number,
        "source_uri": item.source_uri,
        "original_filename": item.original_filename,
        "retrieval_phase": item.retrieval_phase,
        "distance": item.distance,
    }


def _knowledge_item_to_tool_data(item: KnowledgeSearchItem) -> dict:
    payload = _to_knowledge_chunk_payload(item)
    return dict(payload)


def _normalize_knowledge_retrieval_profile(profile: str) -> str:
    normalized = str(profile or "focused").strip().lower()
    return normalized if normalized in KNOWLEDGE_RETRIEVAL_PROFILES else "focused"


def _can_use_knowledge_recall_cache(
    *,
    query: str,
    mode: str,
    cache_query: str,
    cached_items: list[KnowledgeRetrievedChunkPayload],
) -> bool:
    if mode != "hybrid":
        return False
    if not cached_items:
        return False
    return query.strip() == cache_query.strip()


def _filter_ready_cached_knowledge_payloads(
    session: Session,
    cached_items: list[KnowledgeRetrievedChunkPayload],
) -> list[KnowledgeRetrievedChunkPayload]:
    chunk_ids = [int(item.get("chunk_id") or 0) for item in cached_items if int(item.get("chunk_id") or 0)]
    if not chunk_ids:
        return []
    rows = session.exec(
        select(KnowledgeChunk.id)
        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
        .join(KnowledgeSpace, KnowledgeSpace.id == KnowledgeChunk.space_id)
        .where(col(KnowledgeChunk.id).in_(chunk_ids))
        .where(KnowledgeSpace.status == "active")
        .where(KnowledgeDocument.status == "ready")
        .where(KnowledgeChunk.embedding_status == "completed")
    ).all()
    valid_chunk_ids = {int(chunk_id) for chunk_id in rows if chunk_id is not None}
    return [item for item in cached_items if int(item.get("chunk_id") or 0) in valid_chunk_ids]


def _select_knowledge_payloads_from_cache(
    cached_items: list[KnowledgeRetrievedChunkPayload],
    *,
    top_k: int,
    per_document_limit: int,
    retrieval_phase: str,
) -> list[dict]:
    selected: list[dict] = []
    counts: dict[int, int] = {}
    seen_chunk_ids: set[int] = set()
    for item in cached_items:
        chunk_id = int(item.get("chunk_id") or 0)
        document_id = int(item.get("document_id") or 0)
        if not chunk_id or chunk_id in seen_chunk_ids:
            continue
        if counts.get(document_id, 0) >= per_document_limit:
            continue
        payload = dict(item)
        payload["retrieval_phase"] = retrieval_phase
        selected.append(payload)
        seen_chunk_ids.add(chunk_id)
        counts[document_id] = counts.get(document_id, 0) + 1
        if len(selected) >= top_k:
            break
    return selected
