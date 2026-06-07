from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager
from dataclasses import dataclass
import base64
import json
import logging
from copy import deepcopy
import os
from pathlib import Path
import re
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.config import get_stream_writer
from langgraph.types import Send, interrupt
from pydantic import BaseModel, Field
from sqlmodel import Session, col, desc, select

from app.ai.json_utils import parse_json_object
from app.agent.graphs.local_operator.nodes import (
    EXEC_TOOL_NAMES,
    READ_TOOL_NAMES,
    WRITE_TOOL_NAMES,
    _known_read_files_from_observations,
    _known_existing_paths_from_observations,
    _normalize_tool_arguments,
    _observation_to_lines,
)
from app.agent.project_rules import RUNTIME_AGENT_RULES
from app.agent.context import (
    ContextLayer,
    ContextBudget,
    PyramidPromptContext,
    build_adjacent_turn_layer,
    build_core_memory_layer,
    build_current_conversation_window_layer,
    build_current_input_layer,
    build_recent_messages_layer,
    build_retrieved_memory_layer,
    build_summary_layer,
    context_layer_from_payload,
)
from app.agent.graphs.memory_chat.state import (
    AgentTaskPayload,
    AgentThoughtPayload,
    AgentToolActionPayload,
    AgentToolObservationPayload,
    AgentWorldStatePayload,
    ChatMessagePayload,
    ContextLayerPayload,
    ElfBubblePayload,
    KnowledgeRetrievedChunkPayload,
    MemoryChatGraphState,
    MountedKnowledgeSpacePayload,
    RemoteTaskSessionPayload,
    RetrievedChunkPayload,
    TurnMessagePayload,
)
from app.agent.context import build_memory_chat_prompt_context
from app.agent.model import (
    get_agent_chat_model,
    get_agent_chat_model_with_tools,
    get_planner_chat_model,
    get_vision_chat_model,
)
from app.core.config import settings
from app.core.timing import elapsed_ms, emit_timing, now_counter
from app.local_operator.policy import LocalOperatorPolicy
from app.local_operator.tools import create_read_tools
from app.models.chat_message import ChatMessage
from app.models.chat_attachment import ChatAttachment, ChatAttachmentDerivative
from app.models.conversation import Conversation
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument, KnowledgeSpace
from app.models.note import utc_now
from app.rag.search import NoteSearchResult, search_notes, search_notes_keyword
from app.rag.chunking.tokenizer import count_tokens
from app.services.knowledge_mount_service import list_conversation_knowledge_mounts
from app.services.knowledge_search_service import (
    NEED_KNOWLEDGE_MOUNT,
    KnowledgeSearchItem,
    search_mounted_knowledge,
)
from app.services.attachment_service import (
    attach_attachments_to_message,
    get_attachment_or_404,
    load_attachment_context_for_message,
)
from app.services.long_term_memory_service import list_core_memories


SessionFactory = Callable[[], AbstractContextManager[Session]]
logger = logging.getLogger(__name__)

# ReAct 兜底：本地工具连续失败超过这个批次数后，agent 节点直接产出说明性回答，
# 不再让 LLM 重试，防止配合 LangGraph 默认 recursion_limit 仍然耗时 90s+ 才结束。
MAX_CONSECUTIVE_FAILED_TOOL_BATCHES = 3
REQUEST_USER_INPUT_TOOL_NAME = "request_user_input"
USER_INTERRUPT_TOOL_NAMES = {REQUEST_USER_INPUT_TOOL_NAME}
INSPECT_IMAGE_ATTACHMENT_TOOL_NAME = "inspect_image_attachment"


class UserInputOption(BaseModel):
    label: str = Field(description="展示给用户看的选项标题。")
    value: str = Field(description="选中后交给 agent 继续执行的具体答案。")
    description: str = Field(default="", description="一行以内的选项说明，解释影响或取舍。")


class RequestUserInputToolInput(BaseModel):
    questions: list[dict] = Field(
        default_factory=list,
        description="连续展示给用户的结构化问题列表。每项包含 question/options/selection_mode 等字段。",
    )
    question: str = Field(
        default="",
        min_length=0,
        description="兼容旧版单问题提问；当 questions 为空时才使用。",
    )
    options: list[UserInputOption] = Field(
        default_factory=list,
        description="兼容旧版单问题的推荐选项。",
    )
    selection_mode: Literal["single", "multiple"] = Field(
        default="single",
        description="兼容旧版单问题的选择模式。",
    )
    allow_other: bool = Field(default=True, description="兼容旧版单问题：是否允许用户输入自定义答案。")
    other_placeholder: str = Field(
        default="请输入其他答案",
        description="兼容旧版单问题：用户选择 Other 时的输入框占位文本。",
    )


@dataclass(frozen=True)
class RetrievalPlan:
    """检索计划结果。

    plan 节点不只判断“要不要检索”，还负责给出检索 query。
    这样后续可以把多源检索、多 query 检索、worker 并行都挂在 plan 输出之后。
    """

    intent: str
    needs_retrieval: bool
    needs_query_rewrite: bool
    retrieval_query: str
    confidence: float
    reason: str
    source: str = "unknown"


@dataclass(frozen=True)
class NoteRetrievalDecision:
    """个人笔记 L3 的轻量检索决策。"""

    action: Literal["skip", "light", "vector"]
    query: str
    confidence: float
    reason: str
    source: str = "rule"


AnswerGenerator = Callable[
    [str, list[ChatMessagePayload], list[RetrievedChunkPayload], bool, str],
    str,
]
ElfBubbleAnswerGenerator = Callable[
    [str, list[ChatMessagePayload], list[RetrievedChunkPayload], bool, str],
    list[ElfBubblePayload],
]
RetrievalPlanner = Callable[[str, list[ChatMessagePayload]], RetrievalPlan]
NoteRetriever = Callable[..., list[NoteSearchResult]]


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


def build_load_turn_state_node(
    session_factory: SessionFactory,
    *,
    recent_limit: int = 12,
):
    """读取本轮对话的基础状态。

    参数：
      session_factory: 数据库 session 工厂。
      recent_limit: 读取最近多少条消息。MVP 先按条数限制，后续应按 token budget 裁剪。
    """

    def load_turn_state(state: MemoryChatGraphState) -> MemoryChatGraphState:
        conversation_id = _resolve_conversation_id(state)
        current_message_ids = {
            message_id
            for message_id in [
                state.get("user_message_id"),
                state.get("assistant_message_id"),
            ]
            if message_id
        }
        with session_factory() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                raise ValueError(f"Conversation {conversation_id} not found.")
            messages = session.exec(
                select(ChatMessage)
                .where(ChatMessage.conversation_id == conversation_id)
                .order_by(desc(ChatMessage.created_at), desc(ChatMessage.id))
                .limit(recent_limit)
            ).all()
            recent_messages = [
                _to_message_payload(message)
                for message in sorted(messages, key=lambda item: (item.created_at, item.id or 0))
                if message.id not in current_message_ids and message.status == "completed"
            ]
            return {
                "conversation_id": conversation_id,
                "langgraph_thread_id": conversation.langgraph_thread_id,
                "recent_messages": recent_messages,
                "conversation_summary": conversation.summary,
                # 新一轮输入开始时重置派生字段，避免同一个 thread 的上一轮结果污染本轮。
                "intent": "direct",
                "needs_retrieval": False,
                "needs_query_rewrite": False,
                "retrieved_chunks": [],
                "retrieval_query": "",
                "plan_confidence": 0.0,
                "retrieval_reason": "",
                "retrieval_grade": "none",
                "retrieval_grade_reason": "",
                "retrieval_debug": {},
                "mounted_knowledge_spaces": [],
                "needs_knowledge_retrieval": False,
                "knowledge_retrieval_query": "",
                "knowledge_retrieval_reason": "",
                "knowledge_retrieved_chunks": [],
                "knowledge_recall_cache": [],
                "knowledge_retrieval_debug": {},
                "context_l0_layer": {},
                "context_l0_adjacent_layer": {},
                "context_l1_layer": {},
                "context_conversation_window_layer": {},
                "context_l2_layer": {},
                "context_l3_layer": {},
                "context_l3_knowledge_layer": {},
                "context_l4_layer": {},
                "context_lx_attachment_layer": {},
                "prompt_context": "",
                "turn_messages": [
                    {
                        "role": "user",
                        "content": _resolve_user_message(state),
                        "name": "current_user_input",
                        "tool_call_id": None,
                    }
                ],
                # ReAct 工具循环由 LangGraph recursion_limit 与模型 tool_calls 自然控制。
                # 这里保留 tool_budget 字段只为兼容旧 debug payload，不再参与路由。
                "tool_budget": 20,
                "task": {},
                "world_state": _empty_world_state(),
                "verification": {},
                "replan_required": False,
                "consecutive_failed_tools": 0,
                "agent_step_index": 0,
                "agent_decision": {},
                "tool_observations": [],
                "tool_observation_context": "",
                "thought_events": [],
                "answer_mode": state.get("answer_mode", "text"),
                "assistant_answer": "",
                "elf_bubble_answer_parts": [],
                # 保留服务层预创建的消息 ID，最终 persist_messages 会更新这些草稿消息。
                "user_message_id": int(state.get("user_message_id") or 0),
                "assistant_message_id": int(state.get("assistant_message_id") or 0),
                "parent_message_id": int(state.get("parent_message_id") or 0),
                "attachment_ids": list(state.get("attachment_ids") or []),
                "graph_checkpoint_id": None,
                "error": "",
            }

    return load_turn_state


def dispatch_context_workers(state: MemoryChatGraphState) -> list[Send]:
    """分发上下文 worker。

    高层记忆、检索、摘要、单独 L1/L0 调试层和 L1+L0 当前对话窗口彼此没有强依赖，
    适合用 LangGraph Send 并行执行。
    每个 worker 写入独立 channel，避免 list reducer 在同一 conversation thread
    跨轮追加旧 layer。
    """

    return [
        Send("build_l4_core_memory", state),
        Send("build_l3_retrieved_memory", state),
        Send("build_l3_knowledge_context", state),
        Send("build_l2_summary", state),
        Send("build_l1_recent_messages", state),
        Send("build_lx_attachment_context", state),
        Send("build_l0_adjacent_turn", state),
        Send("build_l0_current_input", state),
        Send("build_current_conversation_window", state),
    ]


def build_l4_core_memory_node(session_factory: SessionFactory):
    """构建 L4 核心长期记忆层。"""

    def build_l4_core_memory(state: MemoryChatGraphState) -> MemoryChatGraphState:
        with session_factory() as session:
            core_memories = [
                memory.content
                for memory in list_core_memories(session)
            ]
        layer = build_core_memory_layer(core_memories, _context_budget())
        return {"context_l4_layer": layer.to_payload()}

    return build_l4_core_memory


def build_l3_retrieved_memory_node(
    session_factory: SessionFactory,
    *,
    planner: RetrievalPlanner | None = None,
    retriever: NoteRetriever = search_notes,
    limit: int = 5,
):
    """构建 L3 个人笔记检索层。

    默认每轮执行 cheap recall；只有明确个人记忆意图或可选 planner 要求时才升级向量检索。
    这样保留个人 Agent 的高召回倾向，同时避免每轮 embedding/vector 检索拖慢对话。
    """

    def build_l3_retrieved_memory(state: MemoryChatGraphState) -> MemoryChatGraphState:
        user_message = _resolve_user_message(state)
        total_started_at = now_counter()
        failed_stage = ""
        planner_elapsed_ms = 0
        cheap_recall_elapsed_ms = 0
        retriever_elapsed_ms = 0
        grade_elapsed_ms = 0
        layer_elapsed_ms = 0
        plan = RetrievalPlan(
            intent="rag",
            needs_retrieval=False,
            needs_query_rewrite=False,
            retrieval_query=user_message,
            confidence=1.0,
            reason="个人笔记默认执行轻量关键词召回，必要时升级向量检索。",
            source="cheap_note_recall",
        )
        decision = NoteRetrievalDecision(
            action="light",
            query=user_message,
            confidence=0.6,
            reason="默认执行轻量关键词召回。",
        )
        retrieved_chunks: list[RetrievedChunkPayload] = []
        retrieval_grade: Literal["good", "weak", "poor", "none"] = "none"
        retrieval_grade_reason = "尚未完成个人笔记检索。"
        retrieval_query = user_message

        try:
            if planner is not None:
                failed_stage = "planner"
                planner_started_at = now_counter()
                rewritten_plan = planner(user_message, state.get("recent_messages", []))
                planner_elapsed_ms = elapsed_ms(planner_started_at)
                plan = RetrievalPlan(
                    intent="rag",
                    needs_retrieval=rewritten_plan.needs_retrieval,
                    needs_query_rewrite=rewritten_plan.needs_query_rewrite,
                    retrieval_query=rewritten_plan.retrieval_query or user_message,
                    confidence=rewritten_plan.confidence,
                    reason=(
                        f"{rewritten_plan.reason}；个人笔记默认先执行 cheap recall，"
                        "planner 只用于 query rewrite 或显式升级向量检索。"
                    ),
                    source=rewritten_plan.source,
                )

            retrieval_query = plan.retrieval_query or user_message
            with session_factory() as session:
                failed_stage = "cheap_recall"
                cheap_recall_started_at = now_counter()
                cheap_results = search_notes_keyword(session, query=retrieval_query, limit=limit)
                cheap_recall_elapsed_ms = elapsed_ms(cheap_recall_started_at)
                decision = _decide_note_retrieval(
                    user_message=user_message,
                    retrieval_query=retrieval_query,
                    cheap_results=cheap_results,
                    plan=plan if planner is not None else None,
                )
                if decision.action == "vector":
                    failed_stage = "retriever"
                    retriever_started_at = now_counter()
                    results = retriever(session, query=decision.query, limit=limit)
                    retriever_elapsed_ms = elapsed_ms(retriever_started_at)
                elif decision.action == "light":
                    results = cheap_results
                else:
                    results = []

            retrieval_query = decision.query
            retrieved_chunks = [_to_retrieved_chunk_payload(result) for result in results]
            failed_stage = "grade"
            grade_started_at = now_counter()
            retrieval_grade, retrieval_grade_reason = _grade_retrieval_chunks(retrieved_chunks)
            grade_elapsed_ms = elapsed_ms(grade_started_at)
            needs_retrieval = decision.action != "skip"

            failed_stage = "layer"
            layer_started_at = now_counter()
            layer = build_retrieved_memory_layer(
                retrieved_chunks,
                needs_retrieval,
                retrieval_grade,
                _context_budget(),
            )
            layer_elapsed_ms = elapsed_ms(layer_started_at)
            retrieval_debug = {
                "planner_ms": planner_elapsed_ms,
                "cheap_recall_ms": cheap_recall_elapsed_ms,
                "retriever_ms": retriever_elapsed_ms,
                "grade_ms": grade_elapsed_ms,
                "layer_ms": layer_elapsed_ms,
                "total_ms": elapsed_ms(total_started_at),
                "planner_source": plan.source,
                "retrieval_action": decision.action,
                "decision_source": decision.source,
                "decision_confidence": decision.confidence,
                "decision_reason": decision.reason,
                "needs_retrieval": needs_retrieval,
                "retrieval_query": retrieval_query,
                "retrieved_count": len(retrieved_chunks),
            }
        except Exception as exc:
            # L3 是增强上下文，不应因为 embedding/API/检索链路波动阻断主对话。
            layer_started_at = now_counter()
            layer = build_retrieved_memory_layer([], decision.action != "skip", "none", _context_budget())
            layer_elapsed_ms = elapsed_ms(layer_started_at)
            retrieval_debug = {
                "planner_ms": planner_elapsed_ms,
                "cheap_recall_ms": cheap_recall_elapsed_ms,
                "retriever_ms": retriever_elapsed_ms,
                "grade_ms": grade_elapsed_ms,
                "layer_ms": layer_elapsed_ms,
                "total_ms": elapsed_ms(total_started_at),
                "planner_source": plan.source,
                "retrieval_action": decision.action,
                "decision_source": decision.source,
                "decision_confidence": decision.confidence,
                "decision_reason": decision.reason,
                "needs_retrieval": decision.action != "skip",
                "retrieval_query": retrieval_query,
                "retrieved_count": 0,
                "degraded": True,
                "failed_stage": failed_stage or "unknown",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            plan = RetrievalPlan(
                intent="rag",
                needs_retrieval=decision.action != "skip",
                needs_query_rewrite=False,
                retrieval_query=retrieval_query,
                confidence=0.0,
                reason="L3 检索失败，已降级为直接回答。",
                source="fallback",
            )
            retrieved_chunks = []
            retrieval_grade = "none"
            retrieval_grade_reason = "L3 检索失败，已降级为直接回答。"
            emit_timing("memory_chat.l3_failed", **retrieval_debug)
            logger.exception("memory_chat.l3_failed %s", retrieval_debug)

        logger.info("memory_chat.l3_timing %s", retrieval_debug)
        return {
            "intent": plan.intent,
            "needs_retrieval": bool(retrieval_debug.get("needs_retrieval", plan.needs_retrieval)),
            "needs_query_rewrite": plan.needs_query_rewrite,
            "retrieval_query": retrieval_query,
            "plan_confidence": plan.confidence,
            "retrieval_reason": plan.reason,
            "retrieved_chunks": retrieved_chunks,
            "retrieval_grade": retrieval_grade,
            "retrieval_grade_reason": retrieval_grade_reason,
            "retrieval_debug": retrieval_debug,
            "context_l3_layer": layer.to_payload(),
        }

    return build_l3_retrieved_memory


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
                        search_result = search_mounted_knowledge(
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


def build_l2_summary_node():
    """构建 L2 对话摘要层。"""

    def build_l2_summary(state: MemoryChatGraphState) -> MemoryChatGraphState:
        layer = build_summary_layer(state.get("conversation_summary", ""), _context_budget())
        return {"context_l2_layer": layer.to_payload()}

    return build_l2_summary


def build_l1_recent_messages_node():
    """构建 L1 近期对话窗口层。"""

    def build_l1_recent_messages(state: MemoryChatGraphState) -> MemoryChatGraphState:
        layer = build_recent_messages_layer(state.get("recent_messages", []), _context_budget())
        return {"context_l1_layer": layer.to_payload()}

    return build_l1_recent_messages


def build_lx_attachment_context_node(session_factory: SessionFactory):
    """构建附件派生上下文层。

    原始附件只作为可回源证据保存；这里默认注入 derivative/metadata 文本。
    """

    def build_lx_attachment_context(state: MemoryChatGraphState) -> MemoryChatGraphState:
        conversation_id = _resolve_conversation_id(state)
        user_message_id = int(state.get("user_message_id") or 0) or None
        attachment_ids = [int(item) for item in state.get("attachment_ids", []) if int(item) > 0]
        with session_factory() as session:
            attachment_context = load_attachment_context_for_message(
                session,
                conversation_id=conversation_id,
                message_id=user_message_id,
                attachment_ids=attachment_ids,
            )
            _ensure_current_turn_image_derivatives(session, attachment_context)
            session.commit()
            attachment_context = load_attachment_context_for_message(
                session,
                conversation_id=conversation_id,
                message_id=user_message_id,
                attachment_ids=attachment_ids,
            )
        if not attachment_context:
            content = "本轮没有可用附件。"
        else:
            sections: list[str] = []
            for attachment, derivatives in attachment_context:
                lines = [
                    f"- attachment_id: {attachment.id}",
                    f"  kind: {attachment.kind}",
                    f"  name: {attachment.original_name}",
                    f"  mime_type: {attachment.mime_type}",
                    f"  size_bytes: {attachment.size_bytes}",
                    f"  storage_path: {attachment.storage_path}",
                    f"  source_hash: {attachment.sha256}",
                ]
                if attachment.width and attachment.height:
                    lines.append(f"  image_dimensions: {attachment.width}x{attachment.height}")
                if derivatives:
                    lines.append("  derived:")
                    for derivative in derivatives:
                        derivative_text = str(derivative.content or "").strip()
                        if derivative_text:
                            lines.append(f"    [{derivative.kind}]\n{_indent_text(derivative_text, 4)}")
                else:
                    lines.append("  derived: 暂无派生文本。")
                lines.append("  fallback: 如果派生信息不足，应根据 attachment_id/storage_path 回源重新解析原始附件。")
                sections.append("\n".join(lines))
            budget_tokens = min(_context_budget().summary_tokens, 4000)
            content = _truncate_context_text("\n\n".join(sections), budget_tokens)
        layer = ContextLayer(
            level=0,
            name="附件派生上下文（Lx）",
            content=content,
            budget_tokens=min(_context_budget().summary_tokens, 4000),
            used_tokens=count_tokens(content),
            note="默认基于派生文本回答；如果派生文本不足，必须回源读取原始附件，不能凭摘要猜测。",
        )
        return {"context_lx_attachment_layer": layer.to_payload()}

    return build_lx_attachment_context


def _ensure_current_turn_image_derivatives(
    session: Session,
    attachment_context: list[tuple[ChatAttachment, list[ChatAttachmentDerivative]]],
) -> None:
    for attachment, derivatives in attachment_context:
        if attachment.kind != "image" or attachment.id is None:
            continue
        has_completed_vision = any(
            derivative.kind == "vision"
            and derivative.status == "completed"
            and derivative.source_hash == attachment.sha256
            for derivative in derivatives
        )
        if has_completed_vision:
            continue
        result = _inspect_image_attachment_payload(
            attachment,
            instruction="请分析这张用户本轮上传的图片，提取主要内容、可见文字、布局和关键细节。",
        )
        status = "completed" if result["ok"] else "failed"
        content = str(result["data"].get("analysis") or result["message"] or "").strip()
        if not content:
            content = "图片视觉解析没有返回有效内容。"
        session.add(
            ChatAttachmentDerivative(
                attachment_id=int(attachment.id),
                kind="vision",
                content=content,
                model=settings.attachments_vision_model,
                prompt_version="auto-current-turn-v1",
                source_hash=attachment.sha256,
                status=status,
            )
        )


def _inspect_image_attachment_payload(attachment: ChatAttachment, *, instruction: str) -> dict:
    image_path = Path(attachment.storage_path)
    if not image_path.exists() or not image_path.is_file():
        return {
            "ok": False,
            "message": "图片附件文件不存在，无法解析。",
            "error_code": "ATTACHMENT_FILE_NOT_FOUND",
            "data": {"attachment_id": attachment.id},
        }
    image_bytes = image_path.read_bytes()
    max_bytes = settings.attachments_image_max_mb * 1024 * 1024
    if len(image_bytes) > max_bytes:
        return {
            "ok": False,
            "message": f"图片超过 {settings.attachments_image_max_mb} MB，无法直接送入视觉模型。",
            "error_code": "IMAGE_TOO_LARGE",
            "data": {
                "attachment_id": attachment.id,
                "size_bytes": len(image_bytes),
            },
        }
    mime_type = attachment.mime_type or "image/png"
    prompt = (
        "你是 AiMemo 的图片解析助手。请基于图片真实视觉内容回答，不要臆测看不见的细节。\n"
        "请提取：主要画面、可见文字/OCR、图表或界面结构、和用户问题相关的关键细节。\n"
        f"用户本次解析要求：{instruction.strip() or '分析图片内容'}\n"
        f"附件信息：attachment_id={attachment.id}, name={attachment.original_name}, "
        f"mime_type={mime_type}, size_bytes={len(image_bytes)}, dimensions={attachment.width}x{attachment.height}。"
    )
    data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    try:
        model = get_vision_chat_model()
        response = model.invoke(
            [
                HumanMessage(
                    content=[
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ]
                )
            ]
        )
        analysis = str(response.content or "").strip()
    except Exception as exc:
        logger.exception("inspect_image_attachment_failed attachment_id=%s", attachment.id)
        return {
            "ok": False,
            "message": f"视觉模型解析图片失败：{exc}",
            "error_code": "VISION_MODEL_FAILED",
            "data": {
                "attachment_id": attachment.id,
                "name": attachment.original_name,
                "mime_type": mime_type,
            },
        }
    return {
        "ok": True,
        "message": "图片解析完成。",
        "error_code": "",
        "data": {
            "attachment_id": attachment.id,
            "name": attachment.original_name,
            "mime_type": mime_type,
            "width": attachment.width,
            "height": attachment.height,
            "analysis": analysis,
        },
    }


def build_current_conversation_window_node():
    """构建 L1+L0 当前对话窗口层。

    该层专门给 ReAct agent 和调试 UI 使用：近期消息与当前输入被渲染成
    一段连续对话，避免模型把“上一轮 assistant 给出的路径/正文”和
    “当前用户确认保存”割裂开。
    """

    def build_current_conversation_window(state: MemoryChatGraphState) -> MemoryChatGraphState:
        layer = build_current_conversation_window_layer(
            state.get("recent_messages", []),
            _resolve_user_message(state),
            _context_budget(),
        )
        return {"context_conversation_window_layer": layer.to_payload()}

    return build_current_conversation_window


def build_l0_current_input_node():
    """构建 L0 当前输入层。"""

    def build_l0_current_input(state: MemoryChatGraphState) -> MemoryChatGraphState:
        layer = build_current_input_layer(_resolve_user_message(state))
        return {"context_l0_layer": layer.to_payload()}

    return build_l0_current_input


def build_l0_adjacent_turn_node():
    """构建 L0.5 最近邻接上下文层。"""

    def build_l0_adjacent_turn(state: MemoryChatGraphState) -> MemoryChatGraphState:
        layer = build_adjacent_turn_layer(
            state.get("recent_messages", []),
            _resolve_user_message(state),
            _context_budget(),
        )
        return {"context_l0_adjacent_layer": layer.to_payload()}

    return build_l0_adjacent_turn


def build_agent_node(
    session_factory: SessionFactory,
    answer_generator: AnswerGenerator | None = None,
):
    """ReAct 主 agent 节点。

    该节点替代旧的规则规划和单独回答生成链：
      - 将 L0-L4 上下文、本轮消息流和工具 schema 一起交给模型；
      - 如果模型返回 tool_calls，graph 路由到 tools 节点；
      - 如果没有 tool_calls，则把模型正文作为最终 assistant_answer。
    """

    def agent(state: MemoryChatGraphState) -> MemoryChatGraphState:
        # 步号在进入 agent 节点时 +1；本步内产生的 thought 与下游 tools 节点的 tool_invocation
        # 都挂在这个 step_index 上，前端按 step_index 把"思考 → 工具调用 → 文本回答"串成一段。
        step_index = int(state.get("agent_step_index") or 0) + 1
        consecutive_failed = int(state.get("consecutive_failed_tools") or 0)
        if consecutive_failed >= MAX_CONSECUTIVE_FAILED_TOOL_BATCHES:
            failed_observations = [
                obs for obs in (state.get("tool_observations") or [])
                if not bool(obs.get("ok"))
            ]
            recent_errors = "；".join(
                str(obs.get("message") or obs.get("error_code") or "")
                for obs in failed_observations[-3:]
                if obs
            ) or "本地工具连续失败"
            short_circuit_text = (
                f"本地工具已连续 {consecutive_failed} 批次未能取得有效结果（最近原因：{recent_errors}），"
                "为避免在死循环里继续消耗，先停下来反馈给你。你可以换种描述目标的方式、"
                "或者告诉我具体要操作的文件/命令，我再继续。"
            )
            return {
                "agent_step_index": step_index,
                "turn_messages": [
                    *state.get("turn_messages", []),
                    _turn_message("assistant", short_circuit_text, name="agent"),
                ],
                "agent_decision": {
                    "type": "final_answer",
                    "reason": f"连续 {consecutive_failed} 批工具失败，熔断兜底。",
                },
                "assistant_answer": short_circuit_text,
                "consecutive_failed_tools": 0,
                "thought_events": [
                    *state.get("thought_events", []),
                    _thought(
                        "agent-circuit-break",
                        "工具连续失败，停止重试",
                        f"连续 {consecutive_failed} 批工具失败，跳过 LLM 直接产出兜底回答。",
                        related_node="agent",
                        step_index=step_index,
                    ),
                ],
            }

        if answer_generator is not None:
            assistant_text = answer_generator(
                _resolve_user_message(state),
                state.get("recent_messages", []),
                state.get("retrieved_chunks", []),
                bool(state.get("needs_retrieval", False)),
                state.get("retrieval_grade", "none"),
            )
            return {
                "agent_step_index": step_index,
                "turn_messages": [
                    *state.get("turn_messages", []),
                    _turn_message("assistant", assistant_text, name="agent"),
                ],
                "agent_decision": {
                    "type": "final_answer",
                    "reason": "测试注入 answer_generator，直接生成最终回答。",
                },
                "assistant_answer": assistant_text,
                "thought_events": [
                    *state.get("thought_events", []),
                    _thought(
                        "agent-final",
                        "生成最终回答",
                        "测试注入回答生成器已返回最终答复。",
                        related_node="agent",
                        step_index=step_index,
                    ),
                ],
            }

        tools = _create_react_tools(state, session_factory=session_factory)
        model = get_agent_chat_model_with_tools(list(tools.values()))
        messages = _build_react_agent_messages(state)
        response = model.invoke(messages)
        tool_calls = _extract_ai_tool_calls(response)
        assistant_text = _extract_ai_message_content(response)
        turn_message = _ai_message_to_turn_message(response, fallback_content=assistant_text)

        if tool_calls:
            first_tool = tool_calls[0]
            return {
                "agent_step_index": step_index,
                "turn_messages": [*state.get("turn_messages", []), turn_message],
                "agent_decision": {
                    "type": "tool_call",
                    "reason": f"模型决定调用 {first_tool.get('name') or first_tool.get('tool_name')} 等工具。",
                    "tool_calls": tool_calls,
                },
                "thought_events": [
                    *state.get("thought_events", []),
                    _thought(
                        "agent-call-tool",
                        "调用本地工具",
                        f"模型请求调用 {len(tool_calls)} 个工具。",
                        related_node="agent",
                        related_tool_call_id=str(first_tool.get("id") or "") or None,
                        step_index=step_index,
                    ),
                ],
            }

        coerced_choice_tool_call = _coerce_elf_choice_final_answer_to_tool_call(state, assistant_text)
        if coerced_choice_tool_call:
            coerced_turn_message = _turn_message("assistant", assistant_text, name="agent")
            coerced_turn_message["tool_calls"] = [coerced_choice_tool_call]
            return {
                "agent_step_index": step_index,
                "turn_messages": [*state.get("turn_messages", []), coerced_turn_message],
                "agent_decision": {
                    "type": "tool_call",
                    "reason": "精灵模式下检测到普通文本选择题，转换为 request_user_input 选项卡。",
                    "tool_calls": [coerced_choice_tool_call],
                },
                "thought_events": [
                    *state.get("thought_events", []),
                    _thought(
                        "agent-coerce-elf-choice",
                        "转换为选项卡",
                        "精灵最终回答里包含文本选项，已改走 request_user_input。",
                        related_node="agent",
                        related_tool_call_id=str(coerced_choice_tool_call.get("id") or "") or None,
                        step_index=step_index,
                    ),
                ],
            }

        return {
            "agent_step_index": step_index,
            "turn_messages": [*state.get("turn_messages", []), turn_message],
            "agent_decision": {
                "type": "final_answer",
                "reason": "模型没有请求工具，生成最终回答。",
            },
            "assistant_answer": assistant_text,
            "thought_events": [
                *state.get("thought_events", []),
                _thought(
                    "agent-final",
                    "生成最终回答",
                    "结合上下文和真实工具结果生成最终答复。",
                    related_node="agent",
                    step_index=step_index,
                ),
            ],
        }

    return agent


def route_after_agent(state: MemoryChatGraphState) -> str:
    """ReAct agent 后的条件边。"""

    decision = state.get("agent_decision") or {}
    if decision.get("type") == "tool_call":
        return "tools"
    return route_answer_mode(state)


def build_tools_node(session_factory: SessionFactory):
    """执行 agent 返回的 tool_calls，并把结果追加回消息流。

    工具实际执行仍复用 Local Operator 层，因此审计、敏感文件拦截、workspace 策略、
    read-before-write 和 exec 命令策略都保留；memory_chat 只负责编排 ReAct 回环。
    """

    def tools(state: MemoryChatGraphState) -> MemoryChatGraphState:
        decision = state.get("agent_decision") or {}
        tool_calls = [call for call in decision.get("tool_calls") or [] if isinstance(call, dict)]
        if not tool_calls:
            return {}

        # tools 节点没有自己的 step 概念：它的工作隶属于刚刚那一次 agent 调用。
        # 所以这里复用 state 里 agent 写入的 agent_step_index，所有 thought 都跟着这一步。
        step_index = int(state.get("agent_step_index") or 0)
        tool_names = [
            str(call.get("name") or call.get("tool_name") or "unknown")
            for call in tool_calls
        ]
        logger.info(
            "memory_chat.tools_entered conversation_id=%s step_index=%s tool_count=%s tool_names=%s",
            state.get("conversation_id"),
            step_index,
            len(tool_calls),
            tool_names,
        )
        emit_timing(
            "memory_chat.tools_entered",
            conversation_id=state.get("conversation_id"),
            step_index=step_index,
            tool_count=len(tool_calls),
            tool_names=tool_names,
        )
        observations_before = len(state.get("tool_observations", []))
        working_state: MemoryChatGraphState = dict(state)
        allowed = (
            READ_TOOL_NAMES
            | WRITE_TOOL_NAMES
            | EXEC_TOOL_NAMES
            | USER_INTERRUPT_TOOL_NAMES
            | {"knowledge_search", INSPECT_IMAGE_ATTACHMENT_TOOL_NAME}
        )

        # 每条工具完成后立刻通过 custom stream channel 把它推给上游，
        # 避免必须等整个 tools 节点 update 派发后才能看到所有卡片。
        try:
            stream_writer = get_stream_writer()
        except Exception:
            # 离线 / 非 stream 调用（如单元测试）拿不到 writer，降级为 no-op。
            stream_writer = None

        def _emit_observation(observation: dict) -> None:
            if stream_writer is None or not isinstance(observation, dict):
                return
            try:
                stream_writer(
                    {
                        "kind": "tool_observation",
                        "step_index": step_index,
                        "observation": observation,
                    }
                )
            except Exception:
                # writer 写入失败不应该中断工具执行；前端会在 state_update 兜底里补齐这条卡片。
                pass

        def _emit_running(action: dict) -> None:
            # 工具开始前先 push 一张"运行中"卡片，让前端 ToolCallCard 立即以 running 态显示，
            # 工具完成后再用同一个 tool_call_id push 一张完成态把它覆盖。
            _emit_observation(
                {
                    "tool_call_id": str(action.get("tool_call_id") or ""),
                    "tool_name": str(action.get("tool_name") or ""),
                    "arguments": action.get("arguments") if isinstance(action.get("arguments"), dict) else {},
                    "ok": False,
                    "blocked": False,
                    "error_code": "",
                    "message": "",
                    "running": True,
                }
            )

        def _build_action(index: int, tool_call: dict) -> dict:
            tool_name = str(tool_call.get("name") or tool_call.get("tool_name") or "")
            raw_arguments = dict(tool_call.get("args") or tool_call.get("arguments") or {})
            if tool_name == REQUEST_USER_INPUT_TOOL_NAME:
                arguments = _normalize_request_user_input_arguments(raw_arguments)
            elif tool_name == "knowledge_search":
                arguments = _normalize_knowledge_search_arguments(raw_arguments)
            else:
                arguments = _normalize_tool_arguments(tool_name, raw_arguments)
            return {
                "tool_call_id": str(tool_call.get("id") or f"tool-{index + 1}-{tool_name}"),
                "tool_name": tool_name,
                "arguments": _clean_tool_path_arguments(tool_name, arguments),
                "reason": "ReAct agent requested this tool.",
                "status": "EXECUTING",
            }

        def _invoke_one(snapshot: MemoryChatGraphState, action: dict) -> dict:
            logger.info(
                "memory_chat.tool_start conversation_id=%s step_index=%s tool_call_id=%s tool_name=%s arguments=%s",
                state.get("conversation_id"),
                step_index,
                action.get("tool_call_id"),
                action.get("tool_name"),
                action.get("arguments"),
            )
            _emit_running(action)
            update = _run_agent_tool_action(
                snapshot,
                action=action,
                session_factory=session_factory,
                allowed_tool_names=allowed,
                step_index=step_index,
            )
            # 取本次 update 末尾新增的那条观察，立即向上游派发（显式标记 running=False，
            # 让前端用同一个 tool_call_id 把刚刚那张运行态卡片覆盖为完成/失败态）。
            updated_obs = update.get("tool_observations") or []
            prev_obs = snapshot.get("tool_observations") or []
            for observation in updated_obs[len(prev_obs):]:
                final_observation = dict(observation)
                final_observation["running"] = False
                logger.info(
                    "memory_chat.tool_finish conversation_id=%s step_index=%s tool_call_id=%s tool_name=%s ok=%s error_code=%s message=%s",
                    state.get("conversation_id"),
                    step_index,
                    final_observation.get("tool_call_id"),
                    final_observation.get("tool_name"),
                    final_observation.get("ok"),
                    final_observation.get("error_code"),
                    final_observation.get("message"),
                )
                _emit_observation(final_observation)
            return update

        for index, tool_call in enumerate(tool_calls):
            tool_name = str(tool_call.get("name") or tool_call.get("tool_name") or "")
            if tool_name != REQUEST_USER_INPUT_TOOL_NAME:
                continue
            action = _build_action(index, tool_call)
            update = _invoke_one(working_state, action)
            working_state = {**working_state, **update}
            observations = list(working_state.get("tool_observations", []))
            tool_context = _tool_observations_to_context(observations)
            return {
                "tool_observations": observations,
                "tool_observation_context": tool_context,
                "prompt_context": _append_tool_context(working_state.get("prompt_context", ""), tool_context),
                "turn_messages": working_state.get("turn_messages", []),
                "tool_budget": working_state.get("tool_budget", state.get("tool_budget", 0)),
                "consecutive_failed_tools": 0,
                "thought_events": [
                    *working_state.get("thought_events", []),
                    _thought(
                        f"request-user-input-{action.get('tool_call_id') or step_index or 'choice'}",
                        "用户已补充选择",
                        "已收到用户的选择，继续执行当前任务。",
                        related_node="tools",
                        related_tool_call_id=str(action.get("tool_call_id") or "") or None,
                        status="completed",
                        step_index=step_index,
                    ),
                ],
            }

        # 按"连续的 READ 段"切分 tool_calls：
        # - 连续若干个 READ 工具 → 同一批并行执行（共享同一个 snapshot）
        # - WRITE / EXEC / 未知工具 → 单独串行执行
        # 这样既保留了 read-before-write、exec 命令的顺序依赖语义，
        # 又能让模型一次性发出的多个独立读取真正并行（与 Claude Code 行为对齐）。
        groups: list[list[tuple[int, dict]]] = []
        current_reads: list[tuple[int, dict]] = []
        for index, tool_call in enumerate(tool_calls):
            tool_name = str(tool_call.get("name") or tool_call.get("tool_name") or "")
            if tool_name in READ_TOOL_NAMES or tool_name in {"knowledge_search", INSPECT_IMAGE_ATTACHMENT_TOOL_NAME}:
                current_reads.append((index, tool_call))
            else:
                if current_reads:
                    groups.append(current_reads)
                    current_reads = []
                groups.append([(index, tool_call)])
        if current_reads:
            groups.append(current_reads)

        for group in groups:
            if len(group) == 1:
                action = _build_action(group[0][0], group[0][1])
                update = _invoke_one(working_state, action)
                working_state = {**working_state, **update}
                continue

            # 并行 READ 批：所有线程共享 batch 进入前的 snapshot；
            # 每个线程返回的 update 列表包含 snapshot + 自己那条新条目，
            # 我们按 group 内原顺序切片提取增量后追加回 working_state。
            snapshot = dict(working_state)
            snap_obs_len = len(snapshot.get("tool_observations") or [])
            snap_tm_len = len(snapshot.get("turn_messages") or [])
            snap_th_len = len(snapshot.get("thought_events") or [])
            actions = [_build_action(idx, tc) for idx, tc in group]
            with ThreadPoolExecutor(max_workers=len(actions)) as pool:
                results = list(pool.map(lambda act: _invoke_one(snapshot, act), actions))

            merged_obs = list(working_state.get("tool_observations") or [])
            merged_tm = list(working_state.get("turn_messages") or [])
            merged_th = list(working_state.get("thought_events") or [])
            for upd in results:
                merged_obs.extend((upd.get("tool_observations") or [])[snap_obs_len:])
                merged_tm.extend((upd.get("turn_messages") or [])[snap_tm_len:])
                merged_th.extend((upd.get("thought_events") or [])[snap_th_len:])
            working_state = {
                **working_state,
                "tool_observations": merged_obs,
                "tool_budget": max(int(working_state.get("tool_budget") or 0) - len(actions), 0),
                "turn_messages": merged_tm,
                "thought_events": merged_th,
            }
            for upd in results:
                for key in ["knowledge_recall_cache", "knowledge_retrieval_query", "knowledge_retrieval_debug"]:
                    if key in upd:
                        working_state[key] = upd[key]

        observations = list(working_state.get("tool_observations", []))
        # 本轮新增的 observations；若其中至少一个 ok，本批就算"取得了进展"，清零计数。
        # 全部失败则在原计数上 +1（按 batch 计，避免一次 N 个并行失败被算成 N）。
        new_observations = observations[observations_before:]
        any_success = any(bool(obs.get("ok")) for obs in new_observations)
        prev_failed = int(state.get("consecutive_failed_tools") or 0)
        consecutive_failed_tools = 0 if any_success else prev_failed + 1
        tool_context = _tool_observations_to_context(observations)
        return {
            "tool_observations": observations,
            "tool_observation_context": tool_context,
            "prompt_context": _append_tool_context(working_state.get("prompt_context", ""), tool_context),
            "turn_messages": working_state.get("turn_messages", []),
            "tool_budget": working_state.get("tool_budget", state.get("tool_budget", 0)),
            "consecutive_failed_tools": consecutive_failed_tools,
            "knowledge_recall_cache": working_state.get("knowledge_recall_cache", state.get("knowledge_recall_cache", [])),
            "knowledge_retrieval_query": working_state.get("knowledge_retrieval_query", state.get("knowledge_retrieval_query", "")),
            "knowledge_retrieval_debug": working_state.get("knowledge_retrieval_debug", state.get("knowledge_retrieval_debug", {})),
            "thought_events": [
                *working_state.get("thought_events", []),
                _thought(
                    "tools-finished",
                    "工具执行完成",
                    f"本轮已累计 {len(observations)} 条工具结果。",
                    related_node="tools",
                    step_index=step_index,
                ),
            ],
        }

    return tools


def _create_react_tools(
    state: MemoryChatGraphState,
    *,
    session_factory: SessionFactory,
) -> dict:
    """创建 ReAct agent 可见的本地工具集合。"""

    policy = LocalOperatorPolicy.from_roots(_default_local_operator_workspace_roots())
    tools = create_read_tools(
        session_factory=session_factory,
        policy=policy,
        conversation_id=_resolve_conversation_id(state),
        turn_id=None,
        known_existing_paths=_known_existing_paths_from_observations(state.get("tool_observations", [])),
        known_read_files=_known_read_files_from_observations(state.get("tool_observations", [])),
    )
    tools[REQUEST_USER_INPUT_TOOL_NAME] = _create_request_user_input_tool()
    tools["knowledge_search"] = _create_knowledge_search_tool(state, session_factory=session_factory)
    tools[INSPECT_IMAGE_ATTACHMENT_TOOL_NAME] = _create_inspect_image_attachment_tool(
        state,
        session_factory=session_factory,
    )
    return tools


def _create_request_user_input_tool() -> StructuredTool:
    def request_user_input(
        question: str = "",
        options: list[dict] | None = None,
        allow_other: bool = True,
        selection_mode: str = "single",
        other_placeholder: str = "请输入其他答案",
        questions: list[dict] | None = None,
    ) -> str:
        """Ask the user to choose when required information is missing."""

        return json_dumps_compact(
            {
                "ok": False,
                "tool_name": REQUEST_USER_INPUT_TOOL_NAME,
                "message": "request_user_input is handled by the graph interrupt runtime.",
                "data": {
                    "questions": questions or [],
                    "question": question,
                    "options": options or [],
                    "selection_mode": selection_mode,
                    "allow_other": allow_other,
                    "other_placeholder": other_placeholder,
                },
            }
        )

    return StructuredTool.from_function(
        func=request_user_input,
        name=REQUEST_USER_INPUT_TOOL_NAME,
        description=(
            "当必须让用户补充选择、确认路径、选择方案、提供缺失参数或确认风险操作时调用。"
            "调用后 graph 会暂停并向用户展示选择框；用户回答后会从同一轮继续执行。"
            "不要把“请选择：1...2...3...”写成普通最终回答；需要用户选择时必须调用此工具。"
            "如果需要一次收集多个信息，优先使用 questions 数组，每个 question 都包含自己的 options；"
            "例如同时询问项目目录和项目类型时，传 questions=[{question:'项目放在哪里？', options:[...]}, {question:'项目类型是什么？', options:[...]}]。"
            "单个问题时可以继续使用 question/options。question 必须说明为什么需要用户选择；不能空泛。"
            "每个 options 只放 2-4 个建议选项，推荐项放第一；不要包含 Other，界面会自动追加自定义输入。"
            "如果用户可同时选择多个项目/功能/范围，selection_mode 必须设为 multiple。"
        ),
        args_schema=RequestUserInputToolInput,
    )


def _create_inspect_image_attachment_tool(
    state: MemoryChatGraphState,
    *,
    session_factory: SessionFactory,
) -> StructuredTool:
    conversation_id = _resolve_conversation_id(state)

    def inspect_image_attachment(
        attachment_id: int,
        instruction: str = "请分析这张图片的主要内容、可见文字、布局和对用户问题有帮助的细节。",
    ) -> str:
        """Inspect an image attachment that belongs to the current conversation."""

        try:
            normalized_attachment_id = int(attachment_id)
        except (TypeError, ValueError):
            normalized_attachment_id = 0
        if normalized_attachment_id <= 0:
            return json_dumps_compact(
                {
                    "ok": False,
                    "tool_name": INSPECT_IMAGE_ATTACHMENT_TOOL_NAME,
                    "error_code": "INVALID_ARGUMENT",
                    "message": "attachment_id 必须是当前对话中的图片附件 ID。",
                    "blocked": True,
                    "data": {},
                }
            )
        with session_factory() as session:
            attachment = get_attachment_or_404(
                session,
                conversation_id=conversation_id,
                attachment_id=normalized_attachment_id,
            )
            if attachment.kind != "image":
                return json_dumps_compact(
                    {
                        "ok": False,
                        "tool_name": INSPECT_IMAGE_ATTACHMENT_TOOL_NAME,
                        "error_code": "NOT_IMAGE_ATTACHMENT",
                        "message": "该附件不是图片，不能使用图片解析工具。",
                        "blocked": True,
                        "data": {
                            "attachment_id": normalized_attachment_id,
                            "kind": attachment.kind,
                            "mime_type": attachment.mime_type,
                        },
                    }
                )
            result = _inspect_image_attachment_payload(attachment, instruction=instruction)
        return json_dumps_compact(
            {
                "ok": bool(result["ok"]),
                "tool_name": INSPECT_IMAGE_ATTACHMENT_TOOL_NAME,
                "error_code": str(result.get("error_code") or ""),
                "message": str(result["message"]),
                "blocked": result.get("error_code") in {"IMAGE_TOO_LARGE"},
                "data": result["data"],
            }
        )

    return StructuredTool.from_function(
        func=inspect_image_attachment,
        name=INSPECT_IMAGE_ATTACHMENT_TOOL_NAME,
        description=(
            "解析当前对话中的图片附件，返回图片内容描述、OCR 文字、图表/界面结构和关键细节。"
            "只能传 attachment_id，不能传任意本地路径。"
            "当用户要求分析/描述/识别/OCR 本轮已上传图片，且 Lx 附件派生上下文只有 metadata 或信息不足时，"
            "必须主动调用本工具，不要先问用户是否读取。"
        ),
        args_schema=InspectImageAttachmentToolInput,
    )


class KnowledgeSearchToolInput(BaseModel):
    query: str = Field(min_length=1, description="要在当前对话已挂载知识空间中检索的问题或关键词。")
    top_k: int = Field(default=5, ge=1, le=20, description="最多返回多少条知识片段，默认 5。")
    mode: Literal["hybrid", "vector", "keyword"] = Field(default="hybrid", description="检索模式。")
    retrieval_profile: Literal["focused", "expanded", "deep"] = Field(
        default="focused",
        description="检索档位。focused 默认 5 条且每文档最多 3 条；expanded/deep 用于首轮片段不足时从缓存扩充。",
    )


class InspectImageAttachmentToolInput(BaseModel):
    attachment_id: int = Field(ge=1, description="要解析的当前对话图片附件 ID。")
    instruction: str = Field(
        default="请分析这张图片的主要内容、可见文字、布局和对用户问题有帮助的细节。",
        description="本次图片解析重点，例如 OCR、图表分析、界面说明或整体描述。",
    )


def _create_knowledge_search_tool(
    state: MemoryChatGraphState,
    *,
    session_factory: SessionFactory,
) -> StructuredTool:
    conversation_id = _resolve_conversation_id(state)

    def knowledge_search(
        query: str,
        top_k: int = 5,
        mode: str = "hybrid",
        retrieval_profile: str = "focused",
    ) -> str:
        """Search only the knowledge spaces explicitly mounted to the current conversation."""

        normalized_query = query.strip()
        if not normalized_query:
            return json_dumps_compact(
                {
                    "ok": False,
                    "tool_name": "knowledge_search",
                    "error_code": "INVALID_ARGUMENT",
                    "message": "query 不能为空。",
                    "blocked": True,
                    "data": {"results": []},
                }
            )
        profile = _normalize_knowledge_retrieval_profile(retrieval_profile)
        top_k = max(1, min(int(top_k or KNOWLEDGE_RETRIEVAL_PROFILES[profile]["top_k"]), 20))
        per_document_limit = int(KNOWLEDGE_RETRIEVAL_PROFILES[profile]["per_document_limit"])
        normalized_mode = mode if mode in {"hybrid", "vector", "keyword"} else "hybrid"
        cached_items = list(state.get("knowledge_recall_cache") or [])
        cache_query = str(state.get("knowledge_retrieval_query") or "").strip()
        with session_factory() as session:
            if _can_use_knowledge_recall_cache(
                query=normalized_query,
                mode=normalized_mode,
                cache_query=cache_query,
                cached_items=cached_items,
            ):
                mounted_space_ids = {
                    int(mount.space_id)
                    for mount in list_conversation_knowledge_mounts(session, conversation_id)
                }
                scoped_cache = [
                    item for item in cached_items
                    if int(item.get("space_id") or 0) in mounted_space_ids
                ]
                scoped_cache = _filter_ready_cached_knowledge_payloads(session, scoped_cache)
                items = _select_knowledge_payloads_from_cache(
                    scoped_cache,
                    top_k=top_k,
                    per_document_limit=per_document_limit,
                    retrieval_phase="adaptive_expansion_cache" if profile != "focused" else "cache_reuse",
                )
                if items:
                    return json_dumps_compact(
                        {
                            "ok": True,
                            "tool_name": "knowledge_search",
                            "message": f"已从本轮知库检索缓存中扩充到 {len(items)} 条片段。",
                            "data": {
                                "query": normalized_query,
                                "mode": normalized_mode,
                                "top_k": top_k,
                                "retrieval_profile": profile,
                                "per_document_limit": per_document_limit,
                                "cache_hit": True,
                                "results": items,
                                "_state_update": {
                                    "knowledge_retrieval_query": normalized_query,
                                    "knowledge_recall_cache": scoped_cache,
                                    "knowledge_retrieval_debug_patch": {
                                        "tool_cache_hit": True,
                                        "tool_retrieval_profile": profile,
                                        "tool_top_k": top_k,
                                        "tool_per_document_limit": per_document_limit,
                                        "tool_result_count": len(items),
                                    },
                                },
                            },
                        }
                    )
            result = search_mounted_knowledge(
                session,
                conversation_id=conversation_id,
                query=normalized_query,
                top_k=top_k,
                mode=normalized_mode,  # type: ignore[arg-type]
                per_document_limit=per_document_limit,
            )
        if result.status == NEED_KNOWLEDGE_MOUNT:
            return json_dumps_compact(
                {
                    "ok": False,
                    "tool_name": "knowledge_search",
                    "error_code": NEED_KNOWLEDGE_MOUNT,
                    "message": "当前对话未挂载知识空间，不能搜索全局知库。请先让用户在对话中挂载知识空间。",
                    "blocked": True,
                    "data": {"query": result.query, "results": []},
                }
            )
        items = [_knowledge_item_to_tool_data(item) for item in result.results]
        return json_dumps_compact(
            {
                "ok": True,
                "tool_name": "knowledge_search",
                "message": f"已在当前挂载知库中检索到 {len(items)} 条片段。",
                "data": {
                    "query": result.query,
                    "mode": result.mode,
                    "top_k": result.top_k,
                    "retrieval_profile": profile,
                    "per_document_limit": result.per_document_limit,
                    "cache_hit": False,
                    "results": items,
                    "_state_update": {
                        "knowledge_retrieval_query": result.query,
                        "knowledge_recall_cache": [
                            _to_knowledge_chunk_payload(item)
                            for item in result.recall_cache
                        ],
                        "knowledge_retrieval_debug_patch": {
                            "tool_cache_hit": False,
                            "tool_retrieval_profile": profile,
                            "tool_top_k": result.top_k,
                            "tool_per_document_limit": result.per_document_limit,
                            "tool_recall_cache_count": len(result.recall_cache),
                            "tool_result_count": len(items),
                        },
                    },
                },
            }
        )

    return StructuredTool.from_function(
        func=knowledge_search,
        name="knowledge_search",
        description=(
            "在当前对话显式挂载的知识空间中补充检索资料。"
            "只能搜索当前 conversation 已挂载的知识空间，不能指定 space_id，不能全局搜索。"
            "当初始上下文中的挂载知库片段不足以回答，或需要补充查找某个细节时调用。"
            "如果是同一个问题的片段不足或文档上下文断裂，优先保持相同 query 并使用 retrieval_profile='expanded'；"
            "仍不足且用户需要整篇总结/跨章节分析时，再使用 retrieval_profile='deep'。"
            "工具会优先从本轮 recall_cache 扩充候选，只有 query 变化或缓存不足时才重新检索。"
            "[K1]/[K2] 这类编号仅用于内部定位检索片段；最终回答不要输出裸露编号或单独引用列表。"
            "需要说明来源时，用文档标题或自然语言融入句子。"
        ),
        args_schema=KnowledgeSearchToolInput,
    )


def _build_react_agent_messages(state: MemoryChatGraphState) -> list:
    """组装 ReAct agent 的模型输入。

    注意这里不再做“要不要工具”的规则判断。模型会同时看到系统约束、金字塔上下文、
    当前用户输入、本轮已有 AI/tool 消息，并通过绑定的工具 schema 自行决定。
    """

    messages: list = [
        SystemMessage(content=_build_react_agent_system_prompt()),
        HumanMessage(content=state.get("prompt_context", "")),
    ]
    if state.get("answer_mode") == "elf_bubble":
        messages.append(SystemMessage(content=_build_elf_react_agent_runtime_prompt()))
    task_context = _build_task_runtime_context(state)
    if task_context:
        messages.append(SystemMessage(content=task_context))
    messages.extend(_turn_messages_to_langchain_messages(state.get("turn_messages", [])))
    return messages


def _turn_messages_to_langchain_messages(turn_messages: list[TurnMessagePayload]) -> list:
    """把内部 turn_messages 转成 LangChain 消息。"""

    messages: list = []
    for message in turn_messages:
        role = message.get("role")
        content = str(message.get("content") or "")
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "tool":
            messages.append(
                ToolMessage(
                    content=content,
                    tool_call_id=str(message.get("tool_call_id") or "tool-result"),
                    name=str(message.get("name") or "tool"),
                )
            )
        elif role == "system":
            messages.append(SystemMessage(content=content))
        else:
            tool_calls = message.get("tool_calls") or []
            if tool_calls:
                messages.append(AIMessage(content=content, tool_calls=tool_calls))
            else:
                messages.append(AIMessage(content=content))
    return messages


def _build_task_runtime_context(state: MemoryChatGraphState) -> str:
    """构建 agent 可见的任务运行时状态。"""

    task = state.get("task") or {}
    if not task:
        return ""
    world_state = state.get("world_state") or {}
    verification = state.get("verification") or {}
    payload = {
        "task": task,
        "world_state": world_state,
        "remote_task_session": state.get("remote_task_session") or {},
        "verification": verification,
        "replan_required": bool(state.get("replan_required", False)),
    }
    return (
        "下面是本轮任务运行时状态。你必须基于它决定下一步："
        "如果 replan_required=true，要根据失败原因调整方案，不要原样重试；"
        "如果验收条件尚未满足，继续调用工具；只有目标满足时才最终回答。\n"
        f"{json_dumps_compact(payload)}"
    )


def _build_elf_react_agent_runtime_prompt() -> str:
    return (
        "当前入口是桌面精灵。精灵最终回答会被改写成气泡，但结构化交互不会由气泡自动生成。\n"
        "因此：只要你需要用户从多个方案、路径、范围、确认项或下一步动作里选择，"
        "必须调用 request_user_input。不要在最终回答中用普通文本列出“1/2/3”“A/B/C”让用户口头选择。\n"
        "如果你只是想自然闲聊或追问开放问题，可以直接回答；如果有 2-4 个明确可选项，必须走选项卡。"
    )


def _build_react_agent_system_prompt() -> str:
    """ReAct 主 agent 系统提示词。"""

    roots = _default_local_operator_workspace_roots()
    platform_name = "Windows" if os.name == "nt" else "POSIX"
    shell_name = "powershell/cmd" if platform_name == "Windows" else "bash/sh"
    roots_text = "\n".join(f"- {root}" for root in roots[:8])
    return (
        "你是 AiMemo 的主 ReAct agent。你可以自然聊天，也可以通过本地工具读取文件、"
        "写入文件和执行短时命令。\n\n"
        "工作环境：\n"
        f"- 平台：{platform_name}；默认 shell：{shell_name}。\n"
        f"- 当前授权 workspace roots：\n{roots_text}\n"
        "- 所有工具的 path/root/cwd 参数都必须传绝对路径。Windows 例：E:\\demo；"
        "POSIX 例：/home/user/demo。\n"
        "- 用户明确说 E:\\demo 时，绝不能写成 demo/... 或当前项目下的 e:/Ai记/demo/...；"
        "要原样使用 E:\\demo 作为路径起点。\n\n"
        "核心规则：\n"
        "- 不要先用规则猜测是否需要工具；你看到用户目标后自行决定是否调用工具。\n"
        "- 用户请求涉及本地文件、目录、创建、修改、保存、编译、运行、测试、命令输出时，"
        "必须调用相应工具，不能只靠记忆或语言描述回答。\n"
        "- 读取 PDF/DOCX 文档时必须调用 read_document；读取源码、Markdown、JSON、TXT 等普通文本时调用 read_file。"
        "不要用 exec_command/cat/python 脚本绕过专用文档读取工具。\n"
        "- 用户说“方案一/方案二/采用上面的方案/按你说的/继续/随便你”时，要结合历史上下文"
        "理解这是在执行上一轮 assistant 提出的方案；如果方案涉及本地操作，应调用工具。\n"
        "- 本轮没有成功工具结果时，绝不能声称文件已创建/修改、命令已执行、程序已运行、"
        "也不能编造 stdout、随机数、测试通过或构建成功。\n"
        "- 用户请求分析、描述、识别或 OCR 本轮已上传图片时，如果 `附件派生上下文（Lx）` "
        "只有 metadata/尺寸/路径等信息，必须主动调用 inspect_image_attachment；"
        "不要先问用户是否需要读取图片。只有工具失败时，才说明真实失败原因。\n"
        "- 每次工具调用后必须阅读 ToolMessage 里的 ok/error_code/message/stdout/stderr，再决定下一步。\n"
        "- 工具失败时先诊断根因，再选择下一步；不要原样盲目重试，也不要切换到无关工具碰运气。\n"
        "- 写入已有文本文件或覆盖文本文件前，必须先用 read_file 完整读取目标文件；"
        "get_file_info/list_dir 只能确认存在，不能替代读取正文。\n"
        "read_document 是文档解析工具，结果是提取文本，不等价于完整读取原始二进制，不能用于覆盖写入前的 read-before-write 保护。\n"
        "- 遇到全局工具规则限制时，先按默认规则调用基础工具推进任务；不要一上来就询问是否绕过规则。"
        "如果工具返回结果证明默认规则已经卡住任务，或可靠工具元信息能明确判断默认规则不可能完成，"
        "再进入升级确认：不要反复尝试、不要绕开工具、也不要假装完成，而是调用 request_user_input 申请更高权限授权；"
        "question 说明卡住的是哪条规则、绕过风险和授权范围；"
        "options 至少包含“取消/改用更安全方案”和“确认授权继续”。用户确认后，只能绕过本次明确授权的具体限制；"
        "workspace 越权、敏感文件、删除、命令安全、占位内容等底线保护仍不可绕过。\n"
        "- 升级确认少样本：如果 read_file 返回 full_view=false、truncated=true 或 WRITE_WITH_PARTIAL_READ，"
        "且用户目标是整文件替换一个过大的已有文件，不要分批读取到耗尽上下文。"
        "调用 request_user_input 询问是否允许“未完整读取旧内容就直接整文件覆盖”。"
        "用户明确选择确认后，才可调用 write_file(overwrite=true, confirmed_overwrite_without_read=true)；"
        "未确认时禁止设置该参数。\n"
        "- exec_command 只用于前台非交互命令；读写文件用 read_file/write_file。"
        "前台命令的目标是在本轮拿到 stdout/stderr/exit_code，例如 git status、pytest、npm run build、pip install、python 脚本等。"
        "只要用户要的是本轮结果，就不要擅自后台化。\n"
        "- exec_command_background 只用于会持续存活的服务型任务（flask run、uvicorn、npm start/dev、manage.py runserver、"
        "python http.server 等），不要把“慢”当成后台；后台的定义是会长期运行、占端口或持续输出日志，后续需要回来读状态/停止。"
        "前台命令如果误判为长跑服务会被策略层拦截并提示改用后台。\n"
        "- 远程服务器操作必须工具化：用户目标涉及远程服务器、SSH、SCP、nginx、部署、上传静态页面、"
        "把文件传到服务器或登录服务器修改代码时，必须使用 remote_connectivity_check、remote_upload_file、"
        "remote_exec、remote_verify_http；不要把 ssh/scp/sftp/plink/pscp 拼进 exec_command。\n"
        "- 远程工具只支持非交互 SSH key 或本机 SSH agent。缺少 host、username、remote_path、local_path、"
        "认证方式等关键信息时，必须调用 request_user_input 让用户补充；不要猜服务器地址、用户名或目标目录。\n"
        "- remote_connectivity_check 返回 INTERACTIVE_AUTH_REQUIRED、LOCAL_SSH_NOT_FOUND、LOCAL_SCP_NOT_FOUND "
        "或 host key/密码/权限类错误时，不要继续盲目重试；应调用 request_user_input 让用户选择配置 SSH key、"
        "手动登录准备环境、改用已有凭据或取消远程操作。\n"
        "- 远程修改类任务的完成条件不是“命令看起来执行过”：至少要有成功的 remote_upload_file 或 remote_exec，"
        "并用 remote_exec 查看远程文件/服务状态，或用 remote_verify_http 验证公网访问结果。\n"
        "- 如果任务运行时状态里存在 remote_task_session，必须按它的 current_phase、blocked_reason、next_actions 推进；"
        "status=blocked 时不要继续原样调用远程工具，必须调用 request_user_input 收集认证、目标路径或恢复方案。\n"
        "- 需要临时生成上传文件时，优先使用用户给定的项目路径或系统临时目录；不要为了远程操作把临时文件写进 AiMemo 仓库根目录。\n"
        "- exec_command_background 立即返回 task_id；之后用 read_background_output(task_id) "
        "等 1-2 秒拿首批日志确认 status='running' 且没有报错；任务结束/不再需要时用 "
        "kill_background_task(task_id) 停掉，不要重复 spawn 同一服务。"
        "如果用户要的是当前轮次的最终运行结果，不要启动后台任务后结束本轮。\n"
        "- 用户问“现在跑着哪些服务/后台任务”或者想停掉一个但没给 task_id 时，"
        "先调用 list_background_tasks 看本会话的任务列表（含历史/orphaned），"
        "再根据 task_id 操作；不要凭空猜 task_id，也不要直接 kill。\n"
        "- 任务超过 3 步时，先在内部形成简短计划，并按真实工具结果推进；"
        "如果结果不符合预期，基于错误和已完成步骤调整后续动作。\n"
        "- 如果缺少必须由用户决定的信息（例如新项目/新文件目标目录、多个可行方案、"
        "风险操作是否继续、无法安全默认的配置选择），必须调用 request_user_input，"
        "把 2-4 个建议选项放在 options 里，并允许 other；不要只用普通文字提问后结束本轮。"
        "需要用户做决策时，final_answer 不是合法出口；"
        "禁止在 final_answer 中列出“1. 2. 3.”、“几个解决方案”或“你希望采用哪种方案？”后结束本轮。"
        "唯一合法动作是调用 request_user_input，让 graph 暂停并等待用户选择后继续。"
        "如果工具失败后存在多个可执行恢复方案，例如命令不存在、依赖缺失、端口占用、权限不足、"
        "需要安装工具、配置 PATH、添加 wrapper 或改用另一种启动方式，也必须调用 request_user_input；"
        "不要把这些恢复方案写成普通最终回答。\n"
        "必须保持项目上下文隔离：历史对话里某个项目的目录、技术栈、依赖、配置、数据源、账号、风险授权或用户偏好，"
        "不等于授权以后所有新项目都继承这些条件；除非用户本轮明确说“继续上个项目/同一个项目/沿用上次目录或配置”，"
        "否则遇到新的项目、应用、文件组或独立功能时，不能复用旧项目条件，必须重新确认会影响落地的关键条件。"
        "question 字段就是用户会看到的问题，必须写清楚你为什么暂停以及具体要用户决定什么；"
        "不要留空，也不要写“需要你补充一个选择”这种泛泛提示。"
        "如果用户可以同时选择多个推荐项，selection_mode 设为 multiple。"
        "调用 request_user_input 时应作为本批唯一工具调用，等用户选择后再继续执行。"
        "禁止输出“请选择：1...2...3...”这种普通文本选择题作为最终回答；"
        "这类场景必须走 request_user_input。外置桌面精灵/galgame 式对话同样如此："
        "如果精灵需要用户选择路径、方案或确认风险，不能只用气泡问“选择哪个路径”，"
        "必须调用 request_user_input，让前端渲染可点击选项卡和 Other 输入。\n"
        "- request_user_input 少样本：用户说“创建一个 test.txt 文件，写入 helloworld”，"
        "但没有说明目录时，调用 request_user_input，question=\"test.txt 应该创建在哪个目录下？\"，"
        "options 可包括：label=\"Home 目录\", value=\"/home/<user>/test.txt\", "
        "description=\"不污染当前 AiMemo 仓库\"；以及 label=\"AiMemo 仓库内的明确子路径\", "
        "value=\"/home/<user>/project/AiMemo/<subdir>/test.txt\", description=\"仅当用户确实想把文件放进本项目\"。"
        "不要直接回答路径列表。\n"
        "- request_user_input 少样本：上一轮用户选择 `/home/user/demo1`、React、SQLite 写一个项目；本轮用户说“再做一个记账小程序”。"
        "这属于新的项目，不能默认复用 `/home/user/demo1`、React、SQLite 或上一轮授权，"
        "必须再次调用 request_user_input 询问目标目录，并在技术栈/数据源会影响落地时一并确认。"
        "只有用户本轮说“继续改 demo1/沿用上次目录和技术栈”，才可复用这些条件。\n"
        "- request_user_input 少样本：用户说“给应用加导出功能”，若可同时选择导出 Markdown、PDF、HTML，"
        "调用 request_user_input 且 selection_mode=\"multiple\"；若项目已有唯一导出模式可沿用，则直接执行，不要多问。\n"
        "- request_user_input 少样本：read_file 返回 truncated=true，用户又要求整文件覆盖 `/path/big.json`。"
        "调用 request_user_input，question=\"`/path/big.json` 太大，无法在单次工具调用中完整读取。你是否确认在未完整读取旧内容的情况下直接整文件覆盖？\"，"
        "options 包含 label=\"取消覆盖，改用新路径或更小范围\" 与 label=\"确认直接覆盖旧文件\"。"
        "只有用户选择确认后才设置 confirmed_overwrite_without_read=true。\n"
        "- request_user_input 少样本：exec_command_background 启动 Java 项目后，"
        "read_background_output 返回“系统找不到 mvn 命令”。这不是 final_answer 场景，"
        "而是恢复方案选择场景。调用 request_user_input，question=\"当前系统找不到 Maven，接下来你希望我采用哪种方式继续启动项目？\"，"
        "options 可包括 label=\"安装或配置 Maven\", description=\"适合长期使用 Maven 命令\"；"
        "label=\"为项目添加 Maven Wrapper\", description=\"不依赖全局 Maven，更适合项目自包含\"；"
        "label=\"改用已有 jar 或其他启动方式\", description=\"如果项目已经打包或有替代启动脚本\"。"
        "不要在最终回答里列三条方案让用户回复编号。\n"
        "- 知识库边界：当前对话只能使用用户显式挂载到该对话的知识空间。"
        "不要声称搜索了未挂载的知识库，不要要求 knowledge_search 指定 space_id，"
        "也不要绕过挂载边界做全局知库检索。"
        "只要当前对话已挂载知识空间，dispatch_context_workers 默认会先检索挂载资料；"
        "只有非常明确的闲聊或客观常识问题才会跳过首轮检索。"
        "初始上下文中的 `L3.5 挂载知识空间检索` 是首轮检索结果；"
        "如果这些片段不足以回答，才调用 knowledge_search 做补充检索。"
        "同一问题补充检索时优先使用相同 query 加 retrieval_profile=\"expanded\"，"
        "让工具从本轮 recall_cache 扩充；只有问题角度变化、缓存不足或用户要求深查时，才改写 query 或使用 deep。"
        "[K1]/[K2] 这类编号只用于内部定位检索片段，最终回答不要输出裸露编号或单独引用列表；"
        "需要说明来源时，用文档标题或自然语言融入句子。"
        "如果没有挂载或工具返回 NEED_KNOWLEDGE_MOUNT，应明确说明需要先挂载知识空间。\n"
        "- 多个互不依赖的读取、搜索或信息查询可以在同一轮 tool_calls 中并行发出；"
        "有依赖关系的步骤必须等上一步 ToolMessage 返回后再继续。\n"
        "- 不要把删除、清理、覆盖配置、重建项目作为开局动作；只有定位到具体原因后才做针对性修改。\n"
        "- 最终回答只能基于已知上下文和真实工具结果，不要写”模拟展示”。\n\n"
        f"{RUNTIME_AGENT_RULES}\n\n"
        "表达规则：使用中文，简洁说明你实际完成了什么；如果工具失败，直接说明真实失败原因和下一步。"
    )


def _extract_ai_tool_calls(message) -> list[dict]:
    """提取 AIMessage.tool_calls，规整为普通 dict 列表。"""

    raw_calls = getattr(message, "tool_calls", None) or []
    result: list[dict] = []
    for call in raw_calls:
        if isinstance(call, dict):
            result.append(dict(call))
        else:
            result.append(
                {
                    "id": getattr(call, "id", ""),
                    "name": getattr(call, "name", ""),
                    "args": getattr(call, "args", {}) or {},
                }
            )
    return result


def _extract_ai_message_content(message) -> str:
    """提取 AIMessage 正文，兼容字符串和分段 content。"""

    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(str(item.get("text")))
        return "".join(parts)
    return str(content or "")


def _ai_message_to_turn_message(message, *, fallback_content: str) -> TurnMessagePayload:
    """把 AIMessage 压成可进入 checkpoint 的内部消息。"""

    tool_calls = _extract_ai_tool_calls(message)
    if tool_calls:
        turn_message = _turn_message("assistant", fallback_content, name="agent")
        turn_message["tool_calls"] = tool_calls
        return turn_message
    return _turn_message("assistant", fallback_content, name="agent")


def _coerce_elf_choice_final_answer_to_tool_call(
    state: MemoryChatGraphState,
    assistant_text: str,
) -> dict | None:
    """精灵模式下把明显的普通文本选择题改成 request_user_input。

    这是精灵入口的防护网：模型偶尔会无视工具约束，直接用气泡问“请选择 1/2”。
    那样前端无法渲染选项卡。这里只在外置精灵模式、且能稳定提取出至少两个选项时介入。
    """

    if state.get("answer_mode") != "elf_bubble":
        return None
    text = assistant_text.strip()
    if not _looks_like_unstructured_choice_prompt(text):
        return None

    options = _extract_unstructured_choice_options(text)
    if len(options) < 2:
        return None

    question = _extract_unstructured_choice_question(text)
    if len(question) < 6:
        question = "你希望我按哪个选项继续？"
    tool_call_id = f"elf-choice-{int(state.get('conversation_id') or 0)}-{len(state.get('turn_messages', [])) + 1}"
    return {
        "id": tool_call_id,
        "name": REQUEST_USER_INPUT_TOOL_NAME,
        "args": {
            "question": question,
            "options": options[:4],
            "selection_mode": "single",
            "allow_other": True,
        },
    }


def _looks_like_unstructured_choice_prompt(text: str) -> bool:
    if not text:
        return False
    normalized = re.sub(r"\s+", " ", text)
    choice_keywords = [
        "请选择",
        "选择一个",
        "选一个",
        "选哪",
        "哪个选项",
        "哪种方式",
        "你希望",
        "你想要",
        "要不要",
        "是否",
        "确认",
        "方案",
        "选项",
    ]
    return any(keyword in normalized for keyword in choice_keywords)


def _extract_unstructured_choice_options(text: str) -> list[dict]:
    options: list[dict] = []
    bullet_lines: list[str] = []
    for raw_line in text.splitlines():
        line = _strip_markdown_choice_line(raw_line)
        if not line:
            continue
        numbered = re.match(
            r"^(?:选项\s*)?(?:[A-Da-d]|[1-4]|[一二三四])[\.\)、:：]\s*(?P<body>.+)$",
            line,
        )
        if numbered:
            options.append(_choice_option_from_text(numbered.group("body"), len(options)))
            continue
        bullet = re.match(r"^(?:[-*•]\s+)(?P<body>.+)$", raw_line.strip())
        if bullet:
            bullet_lines.append(bullet.group("body").strip())

    if len(options) < 2 and len(bullet_lines) >= 2:
        options = [_choice_option_from_text(line, index) for index, line in enumerate(bullet_lines[:4])]

    deduped: list[dict] = []
    seen_values: set[str] = set()
    for option in options:
        value = str(option.get("value") or "").strip()
        if not value or value in seen_values:
            continue
        seen_values.add(value)
        deduped.append(option)
    return deduped[:4]


def _choice_option_from_text(text: str, index: int) -> dict:
    cleaned = re.sub(r"\s+", " ", text).strip(" -_*`：:")
    label = cleaned
    description = ""
    split_match = re.match(r"^(?P<label>[^：:]{1,24})[：:]\s*(?P<description>.+)$", cleaned)
    if split_match:
        label = split_match.group("label").strip()
        description = split_match.group("description").strip()
    return {
        "id": f"option-{index + 1}",
        "label": label[:36] or f"选项 {index + 1}",
        "value": cleaned,
        "description": description[:96],
    }


def _extract_unstructured_choice_question(text: str) -> str:
    question_lines: list[str] = []
    for raw_line in text.splitlines():
        line = _strip_markdown_choice_line(raw_line)
        if not line:
            continue
        if re.match(r"^(?:选项\s*)?(?:[A-Da-d]|[1-4]|[一二三四])[\.\)、:：]\s*.+$", line):
            break
        if re.match(r"^(?:[-*•]\s+).+$", raw_line.strip()):
            break
        question_lines.append(line)
    question = " ".join(question_lines).strip()
    question = re.sub(r"(?:可以|请)?(?:从)?(?:下面|以下)(?:几个)?(?:选项|方案)(?:里)?(?:选一个)?[：:]?$", "", question).strip()
    return question[:160]


def _strip_markdown_choice_line(line: str) -> str:
    return line.strip().strip("`").strip()


def _default_local_operator_workspace_roots() -> list[str]:
    """返回默认本地读取 workspace roots。

    启动脚本会 `cd backend` 后再启动 uvicorn；如果直接使用 Path.cwd()，
    Local Operator 就读不到 docs/frontend/desktop 等仓库根目录内容。
    这里根据当前文件位置反推出仓库根目录，并默认加入当前用户 Home。

    读取本身没有 write/exec 的副作用，所以 read-only 阶段默认开放本机固定盘符。
    真正的安全边界放在 LocalOperatorPolicy/LocalFilesystemService：
    敏感文件、数据库、设备路径、UNC 网络路径和大小限制仍会被拦截。
    """

    roots = [Path(__file__).resolve().parents[5], Path.home(), *_local_fixed_drive_roots()]
    roots.extend(Path(root).expanduser() for root in _configured_local_operator_workspace_roots())
    normalized: list[str] = []
    seen: set[str] = set()
    for root in roots:
        resolved = str(root.resolve())
        if resolved not in seen:
            normalized.append(resolved)
            seen.add(resolved)
    return normalized


def _local_fixed_drive_roots() -> list[Path]:
    r"""返回本机固定盘符根目录，用于 read-only Local Operator。

    Windows 上用户经常直接给 `C:\...`、`D:\...` 这样的绝对路径。如果默认只授权
    Home，模型就会在回答层误以为自己“看不到 C 盘”。参考 Claude Code 的设计：
    读取能力应由工具真实执行并返回错误，而不是由模型预先拒绝。
    """

    import os

    if os.name != "nt":
        return [Path("/")]
    roots: list[Path] = []
    for code in range(ord("A"), ord("Z") + 1):
        root = Path(f"{chr(code)}:/")
        if root.exists():
            roots.append(root)
    return roots


def _configured_local_operator_workspace_roots() -> list[str]:
    """解析用户在 .env 中追加的 Local Operator read 根目录。

    支持分号或逗号分隔，例如：
      LOCAL_OPERATOR_WORKSPACE_ROOTS=E:\\Ai记;D:\\资料;~/Documents
    """

    raw_value = settings.local_operator_workspace_roots.strip()
    if not raw_value:
        return []
    return [part.strip() for part in re.split(r"[;,]", raw_value) if part.strip()]


def build_merge_prompt_context_node():
    """汇总上下文 worker 结果，生成最终 prompt_context。

    L1 历史消息和 L0 当前输入必须分开注入。之前把 L1+L0 合成“连续对话窗口”，
    容易让工具 planner 把历史 assistant 草稿误当成本轮指令，导致跨任务串工具。
    """

    def merge_prompt_context(state: MemoryChatGraphState) -> MemoryChatGraphState:
        payloads: list[ContextLayerPayload] = [
            _resolve_context_layer(state, "context_l4_layer"),
            _resolve_context_layer(state, "context_l3_knowledge_layer"),
            _resolve_context_layer(state, "context_l3_layer"),
            _resolve_context_layer(state, "context_l2_layer"),
            _resolve_context_layer(state, "context_l1_layer"),
            _resolve_context_layer(state, "context_lx_attachment_layer"),
            _resolve_context_layer(state, "context_l0_adjacent_layer"),
            _resolve_context_layer(state, "context_l0_layer"),
        ]
        layers = [context_layer_from_payload(dict(payload)) for payload in payloads]
        context = PyramidPromptContext(layers=layers)
        prompt_context = context.to_prompt()
        return {"prompt_context": prompt_context}

    return merge_prompt_context


def build_plan_task_node():
    """把本轮用户输入显式化成 task，供后续工具循环持续引用。

    这里先做确定性轻量计划，不额外调用 LLM。真正的工具选择仍由 ReAct agent 完成；
    这个节点的价值是给 checkpoint/debug 和后续 verify/replan 一个稳定任务对象。
    """

    def plan_task(state: MemoryChatGraphState) -> MemoryChatGraphState:
        user_message = _resolve_user_message(state).strip()
        task_id = f"turn-{state.get('user_message_id') or state.get('conversation_id') or 'current'}"
        task: AgentTaskPayload = {
            "id": task_id,
            "goal": user_message,
            "status": "running",
            "current_step_id": "step-1",
            "steps": [
                {
                    "id": "step-1",
                    "description": _classify_initial_step_description(user_message),
                    "status": "pending",
                    "tool_name": "",
                    "arguments": {},
                    "result_summary": "",
                    "retry_count": 0,
                }
            ],
            "acceptance_criteria": _infer_acceptance_criteria(user_message),
            "assumptions": [],
        }
        update: MemoryChatGraphState = {
            "task": task,
            "world_state": _empty_world_state(),
            "verification": {"status": "pending", "reason": "task planned"},
            "replan_required": False,
            "thought_events": [
                *state.get("thought_events", []),
                _thought(
                    "plan-task",
                    "规划任务",
                    f"目标：{user_message[:120]}",
                    related_node="plan_task",
                    step_index=0,
                ),
            ],
        }
        if _looks_like_remote_task(user_message):
            update["remote_task_session"] = _empty_remote_task_session(f"{task_id}-remote", goal=user_message)
        return update

    return plan_task


def build_observe_tool_result_node():
    """把工具结果吸收进 task/world state。

    tools 节点负责执行；observe 节点负责把结果变成 agent 可持续利用的世界状态。
    """

    def observe_tool_result(state: MemoryChatGraphState) -> MemoryChatGraphState:
        observations = list(state.get("tool_observations") or [])
        world_state = _world_state_from_observations(observations)
        task = _task_with_latest_observation(state.get("task") or {}, observations)
        latest = observations[-1] if observations else {}
        latest_summary = _summarize_tool_observation(latest) if latest else "本轮还没有工具结果。"
        update: MemoryChatGraphState = {
            "world_state": world_state,
            "task": task,
            "thought_events": [
                *state.get("thought_events", []),
                _thought(
                    "observe-tool-result",
                    "吸收工具结果",
                    latest_summary,
                    related_node="observe_tool_result",
                    related_tool_call_id=str(latest.get("tool_call_id") or "") or None,
                    step_index=int(state.get("agent_step_index") or 0),
                ),
            ],
        }
        if state.get("remote_task_session") or _observations_include_remote_tool(observations):
            update["remote_task_session"] = _remote_task_session_from_observations(
                state.get("remote_task_session") or _empty_remote_task_session(
                    f"{(state.get('task') or {}).get('id') or 'turn-current'}-remote",
                    goal=str((state.get("task") or {}).get("goal") or state.get("user_message") or ""),
                ),
                observations,
            )
        return update

    return observe_tool_result


def build_verify_goal_node():
    """基于工具事实做一层轻量验收，防止“工具成功 == 任务成功”。

    第一版只做确定性检查：失败工具会要求 agent 重新规划；没有失败时把状态标为
    ready_for_agent，让下一次 agent 调用基于 ToolMessage 决定继续还是最终回答。
    """

    def verify_goal(state: MemoryChatGraphState) -> MemoryChatGraphState:
        observations = list(state.get("tool_observations") or [])
        latest = observations[-1] if observations else {}
        failed = [obs for obs in observations if not bool(obs.get("ok"))]
        verification = {
            "status": "needs_replan" if latest and not bool(latest.get("ok")) else "ready_for_agent",
            "reason": _verification_reason(state, latest),
            "observation_count": len(observations),
            "failure_count": len(failed),
        }
        remote_session = dict(state.get("remote_task_session") or {})
        if remote_session:
            verification["remote_task_session"] = remote_session
            if remote_session.get("status") == "blocked":
                verification["status"] = "needs_user_input"
                verification["reason"] = (
                    "远程任务已阻塞："
                    f"{remote_session.get('blocked_reason') or '缺少远程目标、认证或路径信息'}。"
                    "下一轮 agent 必须调用 request_user_input 收集恢复方案，不要继续原样重试。"
                )
            elif remote_session.get("status") == "completed":
                verification["status"] = "ready_for_final"
                verification["reason"] = "远程任务上传/执行/验证链路已完成，可以基于真实工具结果总结。"
        task = dict(state.get("task") or {})
        if latest and not bool(latest.get("ok")):
            task["status"] = "running"
        elif observations:
            task["status"] = "running"
        return {
            "verification": verification,
            "task": task,
            "replan_required": verification["status"] == "needs_replan",
            "thought_events": [
                *state.get("thought_events", []),
                _thought(
                    "verify-goal",
                    "验收当前进展",
                    str(verification["reason"]),
                    related_node="verify_goal",
                    step_index=int(state.get("agent_step_index") or 0),
                ),
            ],
        }

    return verify_goal


def route_answer_mode(state: MemoryChatGraphState) -> str:
    """根据 answer_mode 选择回答生成分支。

    ReAct 版普通 text 回答已经由 agent 节点写入 assistant_answer；
    桌面精灵外置聊天仍需要气泡节点重写为 bubble JSON。
    两条分支最后都必须写入 assistant_answer，确保 persist_messages 可以复用。
    """

    if state.get("answer_mode") == "elf_bubble":
        return "generate_elf_bubble_answer"
    return "persist_messages"


def build_generate_elf_bubble_answer_node(
    bubble_answer_generator: ElfBubbleAnswerGenerator | None = None,
):
    """生成桌面精灵气泡回复。

    该节点是 generate_answer 的并行替代分支：它面向外置精灵，要求模型把回答拆成
    多个语义完整的气泡，并为每个气泡给出 emoji。为了让下游持久化保持简单，
    节点仍会把所有气泡 text 合并为 assistant_answer。
    """

    def generate_elf_bubble_answer(state: MemoryChatGraphState) -> MemoryChatGraphState:
        user_message = _resolve_user_message(state)
        recent_messages = state.get("recent_messages", [])
        retrieved_chunks = state.get("retrieved_chunks", [])
        needs_retrieval = bool(state.get("needs_retrieval", False))
        retrieval_grade = state.get("retrieval_grade", "none")
        if bubble_answer_generator is None:
            parts = generate_memory_chat_elf_bubble_answer(
                user_message,
                recent_messages,
                retrieved_chunks,
                needs_retrieval,
                retrieval_grade,
                prompt_context=state.get("prompt_context", ""),
                turn_messages=state.get("turn_messages", []),
            )
        else:
            raw_parts = bubble_answer_generator(
                user_message,
                recent_messages,
                retrieved_chunks,
                needs_retrieval,
                retrieval_grade,
            )
            # 测试桩或后续替代生成器可能直接返回旧版 emoji；这里统一归一化，
            # 保证 graph state、持久化消息和桌面端展示使用同一套表情枚举。
            parts = [
                {
                    "text": part["text"],
                    "emoji": _normalize_elf_emoji(str(part.get("emoji") or "idle_soft")),
                }
                for part in raw_parts
                if part.get("text")
            ]
        return {
            "elf_bubble_answer_parts": parts,
            "assistant_answer": "\n\n".join(part["text"] for part in parts if part.get("text")),
        }

    return generate_elf_bubble_answer


def build_persist_messages_node(session_factory: SessionFactory):
    """把用户消息和 AI 回复写入业务表。

    注意：LangGraph checkpoint 保存的是执行现场；用户可见的消息必须落到 chatmessage。
    流式接口会在 graph 启动前先创建 user/assistant 草稿消息；此节点优先更新草稿。
    非流式接口没有草稿 ID 时，仍沿用创建消息的路径。
    """

    def persist_messages(state: MemoryChatGraphState) -> MemoryChatGraphState:
        conversation_id = _resolve_conversation_id(state)
        user_message = _resolve_user_message(state)
        assistant_answer = state.get("assistant_answer")
        if not assistant_answer:
            raise ValueError("assistant_answer is required before persisting messages.")

        with session_factory() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                raise ValueError(f"Conversation {conversation_id} not found.")

            draft_pair = _load_draft_pair(
                session,
                conversation_id=conversation_id,
                user_message_id=int(state.get("user_message_id") or 0),
                assistant_message_id=int(state.get("assistant_message_id") or 0),
            )
            if draft_pair:
                user, assistant = draft_pair
                user.content = user_message
                user.status = "completed"
                user.token_count = count_tokens(user_message)
                user.updated_at = utc_now()
                assistant.content = assistant_answer
                assistant.status = "completed"
                assistant.token_count = count_tokens(assistant_answer)
                assistant.updated_at = utc_now()
                conversation.updated_at = utc_now()
                conversation.active_task = ""
                session.add(user)
                session.add(assistant)
                session.add(conversation)
                attach_attachments_to_message(
                    session,
                    conversation_id=conversation_id,
                    message_id=user.id or 0,
                    attachment_ids=list(state.get("attachment_ids") or []),
                )
                session.commit()
                return {
                    "user_message_id": user.id or 0,
                    "assistant_message_id": assistant.id or 0,
                }

            existing_pair = _find_existing_tail_pair(session, conversation_id, user_message, assistant_answer)
            if existing_pair:
                return {
                    "user_message_id": existing_pair[0],
                    "assistant_message_id": existing_pair[1],
                }

            parent_id = int(state.get("parent_message_id") or 0) or _latest_message_id(session, conversation_id)
            if parent_id is not None:
                parent = session.get(ChatMessage, parent_id)
                if parent is None or parent.conversation_id != conversation_id:
                    raise ValueError("parent_message_id must reference a message in the same conversation.")
            user = ChatMessage(
                conversation_id=conversation_id,
                role="user",
                content=user_message,
                parent_id=parent_id,
                token_count=count_tokens(user_message),
            )
            session.add(user)
            session.flush()
            if user.id is None:
                raise RuntimeError("User message id was not generated.")

            assistant = ChatMessage(
                conversation_id=conversation_id,
                role="assistant",
                content=assistant_answer,
                parent_id=user.id,
                token_count=count_tokens(assistant_answer),
            )
            session.add(assistant)
            session.flush()
            if assistant.id is None:
                raise RuntimeError("Assistant message id was not generated.")
            attach_attachments_to_message(
                session,
                conversation_id=conversation_id,
                message_id=user.id,
                attachment_ids=list(state.get("attachment_ids") or []),
            )

            conversation.updated_at = utc_now()
            conversation.active_task = ""
            session.add(conversation)
            session.commit()
            return {
                "user_message_id": user.id,
                "assistant_message_id": assistant.id,
            }

    return persist_messages


def default_retrieval_planner(
    user_message: str,
    recent_messages: list[ChatMessagePayload],
) -> RetrievalPlan:
    """默认检索规划器：规则快路径 + LLM 兜底。

    规则明确时不调用额外 LLM；规则不确定时，使用 qwen-turbo 结构化判断，
    并允许模型给出改写后的 retrieval_query。
    """

    rule_result = _rule_plan_retrieval(user_message)
    if rule_result != "uncertain":
        return rule_result
    return _llm_plan_retrieval(user_message, recent_messages)


def _rule_plan_retrieval(user_message: str) -> RetrievalPlan | Literal["uncertain"]:
    normalized = user_message.strip()
    profile_keywords = [
        "我是一个怎么样的人",
        "我是个怎么样的人",
        "我是怎样的人",
        "我是个怎样的人",
        "我是一个什么样的人",
        "我是个什么样的人",
        "你觉得我是",
        "评价一下我",
        "评价我",
        "我的性格",
        "我的特点",
        "我的画像",
        "你了解我",
    ]
    if any(keyword in normalized for keyword in profile_keywords):
        return RetrievalPlan(
            intent="rag",
            needs_retrieval=True,
            needs_query_rewrite=True,
            retrieval_query="用户个人画像 性格特质 生活偏好 近期计划 行为记录",
            confidence=0.92,
            reason="规则判断为个人画像类问题，直接检索用户记忆。",
            source="rule_profile",
        )

    must_retrieve_keywords = [
        "我之前",
        "之前我",
        "上次",
        "以前",
        "记得",
        "我说过",
        "笔记",
        "提到过",
        "有没有",
        "来着",
        "啥来着",
        "什么来着",
        "那个",
        "那件事",
        "那个地方",
        "那个东西",
    ]
    if any(keyword in normalized for keyword in must_retrieve_keywords):
        return RetrievalPlan(
            intent="rag",
            needs_retrieval=True,
            needs_query_rewrite=False,
            retrieval_query=normalized,
            confidence=0.9,
            reason="用户问题包含个人记忆查询线索。",
            source="rule_memory_keyword",
        )

    direct_patterns = ["1+1", "等于几", "天气怎么样", "你好", "hello", "hi"]
    if any(pattern in normalized.lower() for pattern in direct_patterns):
        return RetrievalPlan(
            intent="direct",
            needs_retrieval=False,
            needs_query_rewrite=False,
            retrieval_query="",
            confidence=0.85,
            reason="规则判断为普通问题，不需要查询个人知识库。",
            source="rule_direct",
        )

    return "uncertain"


def _llm_plan_retrieval(
    user_message: str,
    recent_messages: list[ChatMessagePayload],
) -> RetrievalPlan:
    total_started_at = now_counter()
    recent_text = "\n".join(
        f"{message['role']}: {message['content']}" for message in recent_messages[-6:]
    ) or "无"
    prompt_started_at = now_counter()
    prompt = (
        "你是 Ai 记的检索规划器。判断用户问题是否需要查询用户的个人笔记/记忆库，"
        "并在需要时给出适合向量检索的中文 query。\n\n"
        "只返回 JSON，不要输出其他文本。JSON 格式：\n"
        "{"
        "\"intent\":\"direct 或 rag\","
        "\"needs_retrieval\":true,"
        "\"needs_query_rewrite\":false,"
        "\"retrieval_query\":\"用于检索的 query\","
        "\"confidence\":0.0,"
        "\"reason\":\"简短原因\""
        "}\n\n"
        "判断原则：\n"
        "- 如果用户询问自己的过去记录、偏好、计划、笔记内容，需要检索。\n"
        "- 如果是常识、数学、普通闲聊，不需要检索。\n"
        "- 如果用户使用“那个/刚刚/来着”等指代词，结合近期对话改写 query。\n\n"
        f"近期对话：\n{recent_text}\n\n"
        f"用户问题：{user_message}"
    )
    prompt_ms = elapsed_ms(prompt_started_at)
    try:
        model_started_at = now_counter()
        model = get_planner_chat_model()
        model_factory_ms = elapsed_ms(model_started_at)
        invoke_started_at = now_counter()
        response = model.invoke([HumanMessage(content=prompt)])
        invoke_ms = elapsed_ms(invoke_started_at)
        parse_started_at = now_counter()
        payload = parse_json_object(str(response.content))
        parse_ms = elapsed_ms(parse_started_at)
        needs_retrieval = bool(payload.get("needs_retrieval", False))
        retrieval_query = str(payload.get("retrieval_query") or user_message).strip()
        emit_timing(
            "memory_chat.planner_llm_timing",
            total_ms=elapsed_ms(total_started_at),
            prompt_ms=prompt_ms,
            model_factory_ms=model_factory_ms,
            invoke_ms=invoke_ms,
            parse_ms=parse_ms,
            prompt_chars=len(prompt),
            recent_count=len(recent_messages),
            response_chars=len(str(response.content)),
            model=getattr(model, "model_name", ""),
            needs_retrieval=needs_retrieval,
            needs_query_rewrite=bool(payload.get("needs_query_rewrite", False)),
        )
        return RetrievalPlan(
            intent="rag" if needs_retrieval else "direct",
            needs_retrieval=needs_retrieval,
            needs_query_rewrite=bool(payload.get("needs_query_rewrite", False)),
            retrieval_query=retrieval_query if needs_retrieval else "",
            confidence=float(payload.get("confidence", 0.5)),
            reason=str(payload.get("reason") or "LLM 检索规划结果。"),
            source="llm",
        )
    except Exception as exc:
        # 规划失败时走保守策略：不让异常打断聊天，但把含糊问题交给直接回答。
        # 后续可以把该错误写入观测日志。
        emit_timing(
            "memory_chat.planner_llm_timing",
            total_ms=elapsed_ms(total_started_at),
            prompt_ms=locals().get("prompt_ms", 0),
            model_factory_ms=locals().get("model_factory_ms", 0),
            invoke_ms=locals().get("invoke_ms", 0),
            parse_ms=locals().get("parse_ms", 0),
            prompt_chars=len(prompt) if "prompt" in locals() else 0,
            recent_count=len(recent_messages),
            error=repr(exc),
        )
        return RetrievalPlan(
            intent="direct",
            needs_retrieval=False,
            needs_query_rewrite=False,
            retrieval_query="",
            confidence=0.2,
            reason=f"检索规划失败，降级为直接回答：{exc}",
            source="llm_failed",
        )


def _direct_retrieval_plan(reason: str) -> RetrievalPlan:
    return RetrievalPlan(
        intent="direct",
        needs_retrieval=False,
        needs_query_rewrite=False,
        retrieval_query="",
        confidence=0.85,
        reason=reason,
        source="rule_direct",
    )


def _decide_note_retrieval(
    *,
    user_message: str,
    retrieval_query: str,
    cheap_results: list[NoteSearchResult],
    plan: RetrievalPlan | None,
) -> NoteRetrievalDecision:
    """个人笔记检索门控。

    问题不是“需不需要检索”，而是“能不能安全跳过重检索”。
    每轮 cheap recall 已经执行；这里只决定是否跳过、使用 cheap 结果，或升级向量检索。
    """

    normalized = user_message.strip().lower()
    if _is_safe_skip_note_retrieval(normalized):
        return NoteRetrievalDecision(
            action="skip",
            query="",
            confidence=0.95,
            reason="明确闲聊、纯算术或纯格式转换，可以安全跳过个人笔记检索。",
            source="rule_safe_skip",
        )

    if plan is not None and plan.needs_retrieval:
        return NoteRetrievalDecision(
            action="vector",
            query=plan.retrieval_query or retrieval_query,
            confidence=max(plan.confidence, 0.75),
            reason="注入 planner 明确要求个人笔记向量检索。",
            source=plan.source,
        )

    if _has_explicit_personal_memory_intent(normalized):
        return NoteRetrievalDecision(
            action="vector",
            query=retrieval_query,
            confidence=0.9,
            reason="用户明确询问个人记忆、笔记、历史记录或个人画像，需要向量检索。",
            source="rule_explicit_memory",
        )

    if cheap_results:
        return NoteRetrievalDecision(
            action="light",
            query=retrieval_query,
            confidence=0.8,
            reason="轻量关键词召回已有候选，先把候选交给 agent 判断。",
            source="cheap_recall_hit",
        )

    return NoteRetrievalDecision(
        action="light",
        query=retrieval_query,
        confidence=0.55,
        reason="未发现明确个人记忆意图，且轻量召回无候选；不升级向量检索以避免每轮阻塞。",
        source="cheap_recall_miss",
    )


def _is_safe_skip_note_retrieval(normalized_message: str) -> bool:
    compact = re.sub(r"\s+", "", normalized_message)
    if not compact:
        return True
    casual_messages = {
        "你好",
        "您好",
        "hello",
        "hi",
        "hey",
        "晚上好",
        "早上好",
        "下午好",
        "在吗",
        "谢谢",
        "感谢",
        "ok",
        "好的",
    }
    if compact in casual_messages:
        return True
    if re.fullmatch(r"\d+([+\-*/x×÷]\d+)+([=＝]|等于)?(多少|几|是什么|呢|吗)?[\?？]?", compact):
        return True
    if re.fullmatch(r"(把|将).{1,60}(翻译成|译成|改成)(英文|中文|日文|韩文|英语|汉语)", compact):
        return True
    if re.fullmatch(r"(python|js|javascript|java|c\+\+)?怎么打印(hello|helloworld|hello world)", compact):
        return True
    return False


def _has_explicit_personal_memory_intent(normalized_message: str) -> bool:
    triggers = (
        "我之前",
        "我以前",
        "我上次",
        "我说过",
        "我提到",
        "我记录",
        "我写过",
        "我的笔记",
        "笔记里",
        "记录过",
        "记得我",
        "你记得",
        "还记得",
        "上次说",
        "之前说",
        "以前说",
        "来着",
        "我的项目",
        "我的计划",
        "我的偏好",
        "我的性格",
        "评价我",
        "了解我",
        "个人画像",
        "长期记忆",
    )
    return any(trigger in normalized_message for trigger in triggers)




def _clean_tool_path_arguments(tool_name: str, arguments: dict) -> dict:
    """清理模型生成的工具路径参数。

    LLM 经常会把 Markdown 里的反引号一起放进 JSON 路径，例如 `E:/test`。
    文件系统工具会忠实执行这个路径，于是就会创建出 `test`` 这样的目录。
    所以所有进入工具层的 path/root/cwd 都要先做一次轻量清洗。
    """

    cleaned = dict(arguments)
    for key in ["path", "root"]:
        if key in cleaned:
            cleaned[key] = _clean_tool_path(str(cleaned.get(key) or ""))
    if tool_name in {"exec_command", "exec_command_background"} and "cwd" in cleaned:
        cleaned["cwd"] = _clean_tool_path(str(cleaned.get("cwd") or "."))
    return cleaned


def _clean_tool_path(path: str) -> str:
    """清理路径两端常见的自然语言/Markdown 包裹符。"""

    return path.strip().replace("`", "").strip(" \t\r\n").rstrip("）)。；;，,。")


_OTHER_LIKE_LABELS = {
    "其他",
    "other",
    "others",
    "其他答案",
    "其他选项",
    "其他路径",
    "其它",
    "请输入其他答案",
    "请输入其他选项",
    "请输入其他路径",
    "自定义",
    "自定义答案",
    "自定义路径",
    "自定义选项",
    "custom",
}


def _is_other_like_option(label: str, value: str) -> bool:
    """判断 LLM 加的某个选项是否在重复前端会自动追加的 Other 输入项。

    前端永远会在末尾挂一项带输入框的“其他”，LLM 若再加“其他/Other/自定义路径”等就会出现
    一项无输入框的伪“其他”按钮，看起来像 disabled 还可能被默认选中。
    """

    haystack = {label.strip().lower(), value.strip().lower()}
    if not any(haystack):
        return False
    return bool(haystack & _OTHER_LIKE_LABELS)


def _normalize_request_user_input_arguments(arguments: dict) -> dict:
    """保留并规整结构化提问参数。

    Local Operator 的通用 _normalize_tool_arguments 会把未知工具参数清成空 dict。
    request_user_input 是 memory_chat 自己的交互工具，必须单独保留 question/options。
    """

    questions = _normalize_user_input_questions(arguments)
    raw_options = arguments.get("options")
    options: list[dict] = []
    if isinstance(raw_options, list):
        for raw_option in raw_options[:4]:
            if not isinstance(raw_option, dict):
                continue
            label = str(raw_option.get("label") or raw_option.get("value") or "").strip()
            value = str(raw_option.get("value") or label).strip()
            if not label and not value:
                continue
            if _is_other_like_option(label, value):
                # 兜底过滤：即便 prompt 已经禁止，LLM 仍会偶尔塞“其他/自定义路径”等
                # 重复项；这里直接丢弃，前端会在末尾自动追加唯一一份带输入框的 Other。
                continue
            options.append(
                {
                    "id": str(raw_option.get("id") or ""),
                    "label": label or value,
                    "value": value or label,
                    "description": str(raw_option.get("description") or "").strip(),
                }
            )
    raw_selection_mode = arguments.get("selection_mode")
    if raw_selection_mode is None and isinstance(arguments.get("multiSelect"), bool):
        raw_selection_mode = "multiple" if bool(arguments.get("multiSelect")) else "single"
    elif raw_selection_mode is None:
        raw_selection_mode = arguments.get("multiSelect")
    selection_mode = str(raw_selection_mode or "single").strip().lower()
    if selection_mode not in {"single", "multiple"}:
        selection_mode = "multiple" if bool(arguments.get("allow_multiple", False)) else "single"
    return {
        "questions": questions,
        "question": str(arguments.get("question") or "").strip(),
        "options": options,
        "selection_mode": selection_mode,
        "allow_other": bool(arguments.get("allow_other", True)),
        "other_placeholder": str(arguments.get("other_placeholder") or "请输入其他答案").strip(),
    }


def _normalize_knowledge_search_arguments(arguments: dict) -> dict:
    query = str(arguments.get("query") or "").strip()
    profile = str(arguments.get("retrieval_profile") or arguments.get("profile") or "focused").strip().lower()
    if profile not in KNOWLEDGE_RETRIEVAL_PROFILES:
        profile = "focused"
    default_top_k = int(KNOWLEDGE_RETRIEVAL_PROFILES[profile]["top_k"])
    try:
        top_k = int(arguments.get("top_k") or default_top_k)
    except (TypeError, ValueError):
        top_k = default_top_k
    mode = str(arguments.get("mode") or "hybrid").strip().lower()
    if mode not in {"hybrid", "vector", "keyword"}:
        mode = "hybrid"
    normalized = {
        "query": query,
        "top_k": max(1, min(top_k, 20)),
        "mode": mode,
    }
    if "retrieval_profile" in arguments or "profile" in arguments:
        normalized["retrieval_profile"] = profile
    return normalized


def _run_request_user_input_action(
    state: MemoryChatGraphState,
    *,
    action: AgentToolActionPayload,
    step_index: int | None = None,
) -> MemoryChatGraphState:
    arguments = dict(action.get("arguments") or {})
    invalid_reason = _invalid_user_input_request_reason(arguments)
    if invalid_reason:
        observation: AgentToolObservationPayload = {
            "tool_call_id": str(action.get("tool_call_id") or ""),
            "tool_name": REQUEST_USER_INPUT_TOOL_NAME,
            "arguments": arguments,
            "ok": False,
            "data": {},
            "error_code": "INVALID_ARGUMENT",
            "message": (
                f"{invalid_reason}。请重新调用 request_user_input：question 必须是用户能直接理解的具体问题，"
                "options 必须包含 2-4 个具体建议选项；不要用普通文本列选项。"
            ),
            "blocked": False,
        }
        return {
            "tool_observations": [*state.get("tool_observations", []), observation],
            "tool_budget": int(state.get("tool_budget") or 0),
            "turn_messages": [
                *state.get("turn_messages", []),
                _turn_message(
                    "tool",
                    _tool_observation_message(observation),
                    name=REQUEST_USER_INPUT_TOOL_NAME,
                    tool_call_id=str(action.get("tool_call_id") or "") or None,
                ),
            ],
            "thought_events": [
                *state.get("thought_events", []),
                _thought(
                    f"request-user-input-invalid-{action.get('tool_call_id') or step_index or 'choice'}",
                    "提问参数不完整",
                    "request_user_input 缺少具体问题或建议选项，要求 agent 重新发起结构化提问。",
                    related_node="tools",
                    related_tool_call_id=str(action.get("tool_call_id") or "") or None,
                    status="failed",
                    step_index=step_index,
                ),
            ],
        }
    request = _build_user_input_interrupt_payload(arguments, action=action, step_index=step_index)
    resume_value = interrupt(request)
    answer_payload = _normalize_user_input_resume(resume_value, request)
    observation: AgentToolObservationPayload = {
        "tool_call_id": str(action.get("tool_call_id") or ""),
        "tool_name": REQUEST_USER_INPUT_TOOL_NAME,
        "arguments": arguments,
        "ok": True,
        "data": {
            "request": request,
            "answer": answer_payload["answer"],
            "question_answers": answer_payload["question_answers"],
            "selected_option_id": answer_payload["selected_option_id"],
            "selected_option_ids": answer_payload["selected_option_ids"],
            "selected_option_label": answer_payload["selected_option_label"],
            "selected_option_labels": answer_payload["selected_option_labels"],
            "is_other": answer_payload["is_other"],
        },
        "error_code": "",
        "message": f"用户选择：{answer_payload['answer']}",
        "blocked": False,
    }
    return {
        "tool_observations": [*state.get("tool_observations", []), observation],
        "tool_budget": int(state.get("tool_budget") or 0),
        "turn_messages": [
            *state.get("turn_messages", []),
            _turn_message(
                "tool",
                _tool_observation_message(observation),
                name=REQUEST_USER_INPUT_TOOL_NAME,
                tool_call_id=str(action.get("tool_call_id") or "") or None,
            ),
        ],
        "thought_events": [
            *state.get("thought_events", []),
            _thought(
                f"request-user-input-{action.get('tool_call_id') or step_index or 'choice'}",
                "等待用户选择",
                # 多问题路径里 request 只有 `questions=[...]`、没有顶层 `question` 字段；
                # 用 .get + fallback 拼一段摘要，避免 KeyError 把整轮终止。
                "已向用户询问：" + _summarize_user_input_request(request),
                related_node="tools",
                related_tool_call_id=str(action.get("tool_call_id") or "") or None,
                status="interrupted",
                step_index=step_index,
            ),
        ],
    }


def _summarize_user_input_request(request: dict) -> str:
    """从 interrupt 请求里提一段适合 thought 展示的简短摘要。"""

    single_question = str(request.get("question") or "").strip()
    if single_question:
        return single_question
    questions = request.get("questions") if isinstance(request.get("questions"), list) else []
    titles: list[str] = []
    for item in questions:
        if not isinstance(item, dict):
            continue
        text = str(item.get("question") or "").strip()
        if text:
            titles.append(text)
    if not titles:
        return "需要你补充一个选择。"
    if len(titles) == 1:
        return titles[0]
    return "；".join(titles)


def _build_user_input_interrupt_payload(
    arguments: dict,
    *,
    action: AgentToolActionPayload,
    step_index: int | None,
) -> dict:
    questions = _normalize_user_input_questions(arguments)
    if questions:
        return {
            "kind": "user_input",
            "request_id": str(action.get("tool_call_id") or f"user-input-{step_index or 0}"),
            "questions": questions,
            "allow_other": bool(arguments.get("allow_other", True)),
            "other_option": {
                "id": "other",
                "label": "其他",
                "value": "",
                "description": "自己输入一个答案。",
                "placeholder": str(arguments.get("other_placeholder") or "请输入其他答案").strip(),
            },
            "step_index": int(step_index or 0),
        }
    question = str(arguments.get("question") or "").strip()
    raw_options = arguments.get("options")
    options: list[dict] = []
    if isinstance(raw_options, list):
        for index, raw_option in enumerate(raw_options[:4]):
            if not isinstance(raw_option, dict):
                continue
            label = str(raw_option.get("label") or raw_option.get("value") or "").strip()
            value = str(raw_option.get("value") or label).strip()
            if not label and not value:
                continue
            option_id = str(raw_option.get("id") or f"option-{index + 1}")
            options.append(
                {
                    "id": option_id,
                    "label": label or value,
                    "value": value or label,
                    "description": str(raw_option.get("description") or "").strip(),
                    "recommended": index == 0,
                }
            )
    selection_mode = str(arguments.get("selection_mode") or "").strip().lower()
    if selection_mode not in {"single", "multiple"}:
        selection_mode = "multiple" if bool(arguments.get("allow_multiple", False)) else "single"
    return {
        "kind": "user_input",
        "request_id": str(action.get("tool_call_id") or f"user-input-{step_index or 0}"),
        "question": question,
        "options": options,
        "selection_mode": selection_mode,
        "allow_other": bool(arguments.get("allow_other", True)),
        "other_option": {
            "id": "other",
            "label": "其他",
            "value": "",
            "description": "自己输入一个答案。",
            "placeholder": str(arguments.get("other_placeholder") or "请输入其他答案").strip(),
        },
        "step_index": int(step_index or 0),
    }


def _invalid_user_input_request_reason(arguments: dict) -> str:
    if _normalize_user_input_questions(arguments):
        return ""
    question = str(arguments.get("question") or "").strip()
    if len(question) < 6 or question in {"需要你补充一个选择。", "需要你补充一个选择", "请选择"}:
        return "request_user_input 缺少具体问题"
    raw_options = arguments.get("options")
    if not isinstance(raw_options, list):
        return "request_user_input 缺少 options"
    valid_options = []
    for raw_option in raw_options:
        if not isinstance(raw_option, dict):
            continue
        label = str(raw_option.get("label") or raw_option.get("value") or "").strip()
        value = str(raw_option.get("value") or label).strip()
        if label or value:
            valid_options.append(raw_option)
    if len(valid_options) < 2:
        return "request_user_input 至少需要 2 个具体建议选项"
    return ""


def _normalize_user_input_questions(arguments: dict) -> list[dict]:
    raw_questions = arguments.get("questions")
    if not isinstance(raw_questions, list):
        return []
    questions: list[dict] = []
    for index, raw_question in enumerate(raw_questions[:6]):
        if not isinstance(raw_question, dict):
            continue
        question_text = str(raw_question.get("question") or "").strip()
        raw_options = raw_question.get("options")
        options: list[dict] = []
        if isinstance(raw_options, list):
            for option_index, raw_option in enumerate(raw_options[:4]):
                if not isinstance(raw_option, dict):
                    continue
                label = str(raw_option.get("label") or raw_option.get("value") or "").strip()
                value = str(raw_option.get("value") or label).strip()
                if not label and not value:
                    continue
                if _is_other_like_option(label, value):
                    continue
                options.append(
                    {
                        "id": str(raw_option.get("id") or f"question-{index + 1}-option-{option_index + 1}"),
                        "label": label or value,
                        "value": value or label,
                        "description": str(raw_option.get("description") or "").strip(),
                        "recommended": bool(raw_option.get("recommended", option_index == 0)),
                    }
                )
        if len(question_text) < 6 or len(options) < 2:
            continue
        selection_mode = str(raw_question.get("selection_mode") or "single").strip().lower()
        if selection_mode not in {"single", "multiple"}:
            selection_mode = "single"
        questions.append(
            {
                "id": str(raw_question.get("id") or f"question-{index + 1}"),
                "question": question_text,
                "options": options,
                "selection_mode": selection_mode,
                "allow_other": bool(raw_question.get("allow_other", True)),
                "other_placeholder": str(raw_question.get("other_placeholder") or "请输入其他答案").strip(),
            }
        )
    return questions


def _normalize_user_input_resume(resume_value, request: dict) -> dict:
    payload = resume_value if isinstance(resume_value, dict) else {"answer": str(resume_value or "")}
    questions = request.get("questions") if isinstance(request.get("questions"), list) else []
    if questions:
        return _normalize_multi_user_input_resume(payload, request, questions)
    raw_ids = payload.get("selected_option_ids")
    if isinstance(raw_ids, list):
        selected_option_ids = [str(item) for item in raw_ids if str(item)]
    else:
        selected_option_ids = []
    legacy_id = str(payload.get("selected_option_id") or payload.get("option_id") or "")
    if legacy_id and legacy_id not in selected_option_ids:
        selected_option_ids.append(legacy_id)
    answer = str(payload.get("answer") or "").strip()
    selected_option_labels: list[str] = []
    selected_option_values: list[str] = []
    is_other = "other" in selected_option_ids
    options = request.get("options") if isinstance(request.get("options"), list) else []
    options_by_id = {
        str(option.get("id") or ""): option
        for option in options
        if isinstance(option, dict)
    }
    if not selected_option_ids and request.get("selection_mode") != "multiple" and options:
        first_id = str(options[0].get("id") or "")
        if first_id:
            selected_option_ids.append(first_id)
    for option_id in selected_option_ids:
        if option_id == "other":
            continue
        option = options_by_id.get(option_id)
        if not isinstance(option, dict):
            continue
        label = str(option.get("label") or "")
        value = str(option.get("value") or label)
        if label:
            selected_option_labels.append(label)
        if value:
            selected_option_values.append(value)
    other_text = str(payload.get("other_text") or "").strip()
    if not answer:
        answer_parts = [*selected_option_values]
        if is_other and other_text:
            answer_parts.append(other_text)
        answer = "\n".join(answer_parts).strip()
    if not answer:
        answer = "继续"
    selected_option_id = selected_option_ids[0] if selected_option_ids else "other"
    return {
        "answer": answer,
        "question_answers": [],
        "selected_option_id": selected_option_id,
        "selected_option_ids": selected_option_ids or [selected_option_id],
        "selected_option_label": selected_option_labels[0] if selected_option_labels else ("其他" if is_other else answer),
        "selected_option_labels": selected_option_labels or (["其他"] if is_other else [answer]),
        "is_other": is_other,
    }


def _normalize_multi_user_input_resume(payload: dict, request: dict, questions: list[dict]) -> dict:
    """把多问题选择结果还原成 agent 可读的逐题答案。

    前端/桌面端会一次性提交多个问题的选择。这里保留每题的 question_id、问题文本和答案，
    避免工具 observation 只剩几行值，导致 agent 分不清“哪个答案对应哪个问题”。
    """

    raw_question_answers = payload.get("question_answers")
    answer_items: list[dict] = []
    if isinstance(raw_question_answers, list):
        answer_items = [item for item in raw_question_answers if isinstance(item, dict)]
    raw_answers = payload.get("answers")
    fallback_answers = [str(item).strip() for item in raw_answers] if isinstance(raw_answers, list) else []
    answers_by_id = {
        str(item.get("question_id") or item.get("id") or ""): item
        for item in answer_items
        if str(item.get("question_id") or item.get("id") or "")
    }
    normalized_items: list[dict] = []
    answer_lines: list[str] = []
    all_selected_ids: list[str] = []
    all_selected_labels: list[str] = []
    any_other = False

    for index, question in enumerate(questions):
        question_id = str(question.get("id") or f"question-{index + 1}")
        item = answers_by_id.get(question_id)
        if item is None and index < len(answer_items):
            item = answer_items[index]
        if item is None:
            item = {}
        selected_ids = _string_list(item.get("selected_option_ids"))
        legacy_id = str(item.get("selected_option_id") or "").strip()
        if legacy_id and legacy_id not in selected_ids:
            selected_ids.append(legacy_id)
        options = question.get("options") if isinstance(question.get("options"), list) else []
        options_by_id = {
            str(option.get("id") or ""): option
            for option in options
            if isinstance(option, dict)
        }
        if not selected_ids and question.get("selection_mode") != "multiple" and options:
            first_id = str(options[0].get("id") or "")
            if first_id:
                selected_ids.append(first_id)
        selected_labels: list[str] = []
        selected_values: list[str] = []
        for option_id in selected_ids:
            if option_id == "other":
                continue
            option = options_by_id.get(option_id)
            if not isinstance(option, dict):
                continue
            label = str(option.get("label") or "").strip()
            value = str(option.get("value") or label).strip()
            if label:
                selected_labels.append(label)
            if value:
                selected_values.append(value)
        other_text = str(item.get("other_text") or "").strip()
        is_other = "other" in selected_ids or bool(other_text)
        any_other = any_other or is_other
        answer = str(item.get("answer") or "").strip()
        if not answer and index < len(fallback_answers):
            answer = fallback_answers[index]
        if not answer:
            parts = [*selected_values]
            if other_text:
                parts.append(other_text)
            answer = "\n".join(parts).strip()
        if not answer:
            answer = "继续"
        question_text = str(question.get("question") or question_id)
        normalized_item = {
            "question_id": question_id,
            "question": question_text,
            "answer": answer,
            "selected_option_id": selected_ids[0] if selected_ids else "other",
            "selected_option_ids": selected_ids or ["other"],
            "selected_option_labels": selected_labels or (["其他"] if is_other else [answer]),
            "other_text": other_text,
            "is_other": is_other,
        }
        normalized_items.append(normalized_item)
        answer_lines.append(f"{index + 1}. {question_text}\n答：{answer}")
        all_selected_ids.extend(normalized_item["selected_option_ids"])
        all_selected_labels.extend(normalized_item["selected_option_labels"])

    compact_answer = str(payload.get("answer") or "").strip() or "\n".join(answer_lines).strip() or "继续"
    return {
        "answer": compact_answer,
        "question_answers": normalized_items,
        "selected_option_id": all_selected_ids[0] if all_selected_ids else "other",
        "selected_option_ids": all_selected_ids or ["other"],
        "selected_option_label": all_selected_labels[0] if all_selected_labels else ("其他" if any_other else compact_answer),
        "selected_option_labels": all_selected_labels or (["其他"] if any_other else [compact_answer]),
        "is_other": any_other,
    }


def _string_list(value) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


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


def _context_budget() -> ContextBudget:
    return settings.context_pyramid_budget


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


def _run_agent_tool_action(
    state: MemoryChatGraphState,
    *,
    action: AgentToolActionPayload,
    session_factory: SessionFactory,
    allowed_tool_names: set[str],
    step_index: int | None = None,
) -> MemoryChatGraphState:
    """执行主对话循环中的当前工具 action。

    工具仍通过 LangChain @tool.invoke() 调用，审计、路径策略、敏感文件拦截都复用
    `app.local_operator` 层。这样主 graph 只负责编排，不直接碰文件系统。
    """

    tool_name = str(action.get("tool_name") or "")
    arguments = dict(action.get("arguments") or {})
    tool_call_id = str(action.get("tool_call_id") or "")

    if tool_name == REQUEST_USER_INPUT_TOOL_NAME:
        return _run_request_user_input_action(state, action=action, step_index=step_index)

    if tool_name not in allowed_tool_names:
        observation: AgentToolObservationPayload = {
            "tool_call_id": tool_call_id,
            "tool_name": tool_name,
            "arguments": arguments,
            "ok": False,
            "data": {},
            "error_code": "INVALID_ARGUMENT",
            "message": f"工具 `{tool_name}` 不属于当前执行分支。",
            "blocked": True,
        }
    else:
        policy = LocalOperatorPolicy.from_roots(_default_local_operator_workspace_roots())
        tools = create_read_tools(
            session_factory=session_factory,
            policy=policy,
            conversation_id=_resolve_conversation_id(state),
            turn_id=None,
            known_existing_paths=_known_existing_paths_from_observations(state.get("tool_observations", [])),
            known_read_files=_known_read_files_from_observations(state.get("tool_observations", [])),
        )
        tools["knowledge_search"] = _create_knowledge_search_tool(state, session_factory=session_factory)
        tools[INSPECT_IMAGE_ATTACHMENT_TOOL_NAME] = _create_inspect_image_attachment_tool(
            state,
            session_factory=session_factory,
        )
        if tool_name not in tools:
            observation = {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "arguments": arguments,
                "ok": False,
                "data": {},
                "error_code": "INVALID_ARGUMENT",
                "message": f"未知本地工具：{tool_name}",
                "blocked": True,
            }
        else:
            try:
                raw_result = tools[tool_name].invoke(arguments)
                payload = parse_json_object(str(raw_result))
                data = dict(payload.get("data") or {})
                state_update = data.pop("_state_update", {})
                observation = {
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "ok": bool(payload.get("ok")),
                    "data": data,
                    "error_code": str(payload.get("error_code") or ""),
                    "message": str(payload.get("message") or ""),
                    "blocked": bool(payload.get("blocked", False)),
                }
            except Exception as exc:
                # 工具内部抛异常（如 pydantic ValidationError、子进程启动失败、网络断开等）必须收敛为
                # 失败观测，否则会穿透到 graph 让 LangGraph 抛错+流式 SSE 中断+下一轮无法熔断。
                logger.exception("agent tool %s invoke raised", tool_name)
                observation = {
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                    "arguments": arguments,
                    "ok": False,
                    "data": {},
                    "error_code": f"{tool_name.upper()}_INVOKE_FAILED",
                    "message": f"工具 {tool_name} 调用失败：{exc}",
                    "blocked": False,
                }

    update: MemoryChatGraphState = {
        "tool_observations": [*state.get("tool_observations", []), observation],
        "tool_budget": max(int(state.get("tool_budget") or 0) - 1, 0),
        "turn_messages": [
            *state.get("turn_messages", []),
            _turn_message(
                "tool",
                _tool_observation_message(observation),
                name=tool_name,
                tool_call_id=tool_call_id or None,
            ),
        ],
        "thought_events": [
            *state.get("thought_events", []),
            _thought(
                f"run-{tool_call_id or tool_name}",
                f"执行工具 {tool_name}",
                _summarize_tool_observation(observation),
                related_node="run_tool",
                related_tool_call_id=tool_call_id or None,
                step_index=step_index,
            ),
        ],
    }
    if isinstance(locals().get("state_update"), dict) and state_update:
        if isinstance(state_update.get("knowledge_recall_cache"), list):
            update["knowledge_recall_cache"] = state_update["knowledge_recall_cache"]
        if state_update.get("knowledge_retrieval_query") is not None:
            update["knowledge_retrieval_query"] = str(state_update.get("knowledge_retrieval_query") or "")
        debug_patch = state_update.get("knowledge_retrieval_debug_patch")
        if isinstance(debug_patch, dict):
            update["knowledge_retrieval_debug"] = {
                **dict(state.get("knowledge_retrieval_debug") or {}),
                **debug_patch,
            }
    return update


def _tool_observations_to_context(observations: list[AgentToolObservationPayload]) -> str:
    """把工具观察结果转成最终回答模型可读的上下文。"""

    if not observations:
        return ""
    lines = ["## 本地工具调用结果"]
    for observation in observations:
        lines.extend(_observation_to_lines(observation))  # type: ignore[arg-type]
    return "\n".join(lines)


def _append_tool_context(prompt_context: str, tool_context: str) -> str:
    """把本轮工具观察追加到 prompt_context，避免最终回答凭空发挥。"""

    if not tool_context.strip():
        return prompt_context
    if tool_context in prompt_context:
        return prompt_context
    return f"{prompt_context}\n\n{tool_context}" if prompt_context else tool_context


def _empty_world_state() -> AgentWorldStatePayload:
    return {
        "known_paths": {},
        "command_results": [],
        "background_tasks": [],
        "observations": [],
        "failures": [],
    }


REMOTE_TOOL_NAMES = {
    "remote_connectivity_check",
    "remote_upload_file",
    "remote_exec",
    "remote_verify_http",
}
REMOTE_PHASES: list[tuple[str, str]] = [
    ("collect_target", "收集远程目标"),
    ("collect_auth", "确认认证能力"),
    ("prepare_artifact", "准备本地/远程产物"),
    ("transfer", "传输文件"),
    ("remote_apply", "远程应用变更"),
    ("verify", "验证远程结果"),
]


def _looks_like_remote_task(text: str) -> bool:
    lowered = text.lower()
    remote_tokens = ["远程", "服务器", "ssh", "scp", "sftp", "nginx", "部署", "上传", "传到", "登录服务器"]
    return any(token in lowered for token in remote_tokens)


def _empty_remote_task_session(session_id: str, *, goal: str = "") -> RemoteTaskSessionPayload:
    return {
        "id": session_id,
        "status": "collecting_target",
        "current_phase": "collect_target",
        "target": {"goal": goal},
        "auth": {"method": "ssh_key_or_agent", "status": "unknown"},
        "artifacts": {},
        "phases": [
            {
                "id": phase_id,
                "label": label,
                "status": "pending",
                "tool_name": "",
                "summary": "",
                "error_code": "",
            }
            for phase_id, label in REMOTE_PHASES
        ],
        "blocked_reason": "",
        "next_actions": [],
    }


def _classify_initial_step_description(user_message: str) -> str:
    text = user_message.lower()
    if _looks_like_remote_task(user_message):
        return "确认远程目标、认证方式并按上传/执行/验证闭环推进"
    if any(token in text for token in ["创建", "新建", "写入", "保存", "write"]):
        return "确认目标与路径后写入文件"
    if any(token in text for token in ["运行", "执行", "编译", "测试", "run", "test", "build"]):
        return "执行命令并检查结果"
    if any(token in text for token in ["读取", "查看", "搜索", "列出", "read", "list", "search"]):
        return "读取本地信息并回答"
    return "理解用户目标并选择下一步行动"


def _infer_acceptance_criteria(user_message: str) -> list[str]:
    criteria = ["最终回答必须基于真实上下文或工具结果。"]
    text = user_message.lower()
    remote_tokens = ["远程", "服务器", "ssh", "scp", "sftp", "nginx", "部署", "上传", "传到", "登录服务器"]
    if any(token in text for token in ["创建", "新建", "写入", "保存", "write"]):
        criteria.append("如果需要落地文件，必须存在成功 write_file observation。")
    if any(token in text for token in ["运行", "执行", "编译", "测试", "run", "test", "build"]):
        criteria.append("如果需要运行结果，必须引用成功或失败的 exec/background observation。")
    if any(token in text for token in ["读取", "查看", "搜索", "列出", "read", "list", "search"]):
        criteria.append("如果回答本地文件内容，必须存在成功 read/list/search observation。")
    if any(token in text for token in remote_tokens):
        criteria.append("远程服务器操作必须使用 remote_* 工具，不能用 exec_command 直接执行 ssh/scp。")
        criteria.append("远程修改或部署完成前，必须存在成功 remote_upload_file/remote_exec，并通过 remote_exec 或 remote_verify_http 验证。")
        criteria.append("缺少远程 host、username、路径或认证方式时，必须调用 request_user_input。")
    if any(token in text for token in ["目录", "路径", "放在哪", "创建一个", "新建一个"]):
        criteria.append("缺少目标路径或存在多个合理选择时，必须调用 request_user_input。")
    return criteria


def _world_state_from_observations(observations: list[AgentToolObservationPayload]) -> AgentWorldStatePayload:
    world = _empty_world_state()
    for observation in observations:
        data = dict(observation.get("data") or {})
        tool_name = str(observation.get("tool_name") or "")
        compact = {
            "tool_call_id": observation.get("tool_call_id", ""),
            "tool_name": tool_name,
            "ok": bool(observation.get("ok")),
            "error_code": observation.get("error_code", ""),
            "message": observation.get("message", ""),
            "data": _compact_observation_data_for_world(data),
        }
        world["observations"].append(compact)
        if not observation.get("ok"):
            world["failures"].append(compact)
        path = str(data.get("path") or data.get("relative_path") or "")
        if path:
            world["known_paths"][path] = {
                "tool_name": tool_name,
                "ok": bool(observation.get("ok")),
                "exists": data.get("exists", True),
                "size": data.get("size"),
                "modified_at": data.get("modified_at", ""),
            }
        if tool_name == "exec_command":
            world["command_results"].append(
                {
                    "command": data.get("command", ""),
                    "cwd": data.get("cwd", ""),
                    "exit_code": data.get("exit_code"),
                    "ok": bool(observation.get("ok")),
                    "timed_out": data.get("timed_out", False),
                    "stdout_preview": str(data.get("stdout") or "")[:500],
                    "stderr_preview": str(data.get("stderr") or "")[:500],
                }
            )
        if tool_name.startswith("remote_"):
            world["command_results"].append(
                {
                    "tool_name": tool_name,
                    "host": data.get("host", ""),
                    "username": data.get("username", ""),
                    "remote_path": data.get("remote_path", ""),
                    "remote_command": data.get("remote_command", ""),
                    "url": data.get("url", ""),
                    "exit_code": data.get("exit_code"),
                    "ok": bool(observation.get("ok")),
                    "timed_out": data.get("timed_out", False),
                    "stdout_preview": str(data.get("stdout") or data.get("response_preview") or "")[:500],
                    "stderr_preview": str(data.get("stderr") or "")[:500],
                }
            )
        if tool_name in {"exec_command_background", "read_background_output", "kill_background_task", "list_background_tasks"}:
            task_id = str(data.get("task_id") or "")
            if task_id:
                world["background_tasks"].append(
                    {
                        "task_id": task_id,
                        "status": data.get("status", ""),
                        "command": data.get("command", ""),
                        "ok": bool(observation.get("ok")),
                    }
                )
    return world


def _observations_include_remote_tool(observations: list[AgentToolObservationPayload]) -> bool:
    return any(str(obs.get("tool_name") or "") in REMOTE_TOOL_NAMES for obs in observations)


def _remote_task_session_from_observations(
    session: RemoteTaskSessionPayload,
    observations: list[AgentToolObservationPayload],
) -> RemoteTaskSessionPayload:
    updated: RemoteTaskSessionPayload = deepcopy(dict(session))
    phases = _remote_phase_map(updated.get("phases") or [])
    target = dict(updated.get("target") or {})
    auth = dict(updated.get("auth") or {"method": "ssh_key_or_agent", "status": "unknown"})
    artifacts = dict(updated.get("artifacts") or {})
    blocked_reason = ""
    next_actions: list[str] = []

    for observation in observations:
        tool_name = str(observation.get("tool_name") or "")
        if tool_name not in REMOTE_TOOL_NAMES:
            if tool_name == "write_file" and observation.get("ok"):
                data = dict(observation.get("data") or {})
                artifacts["local_path"] = data.get("path") or data.get("relative_path") or artifacts.get("local_path", "")
                _mark_remote_phase(
                    phases,
                    "prepare_artifact",
                    "completed",
                    tool_name=tool_name,
                    summary=f"本地产物已准备：{artifacts.get('local_path') or 'unknown'}",
                )
            continue

        data = dict(observation.get("data") or {})
        args = dict(observation.get("arguments") or {})
        _merge_remote_target(target, data)
        _merge_remote_target(target, args)
        if data.get("local_path") or args.get("local_path"):
            artifacts["local_path"] = data.get("local_path") or args.get("local_path")
        if data.get("remote_path") or args.get("remote_path"):
            target["remote_path"] = data.get("remote_path") or args.get("remote_path")
        if data.get("url") or args.get("url"):
            target["url"] = data.get("url") or args.get("url")

        ok = bool(observation.get("ok"))
        error_code = str(observation.get("error_code") or "")
        message = str(observation.get("message") or "")
        phase_id = _remote_phase_for_tool(tool_name)
        phase_status = "completed" if ok else ("blocked" if bool(observation.get("blocked")) else "failed")
        _mark_remote_phase(
            phases,
            phase_id,
            phase_status,
            tool_name=tool_name,
            summary=_summarize_tool_observation(observation),
            error_code=error_code,
        )

        if tool_name == "remote_connectivity_check":
            auth["status"] = "ready" if ok else "blocked" if observation.get("blocked") else "failed"
            auth["error_code"] = error_code
        elif tool_name == "remote_upload_file" and ok:
            _mark_remote_phase(
                phases,
                "prepare_artifact",
                "completed",
                tool_name=tool_name,
                summary="上传已使用本地产物，产物准备完成。",
            )
        elif tool_name == "remote_verify_http" and ok:
            target["verified_url"] = data.get("url") or args.get("url") or ""

        if not ok and (observation.get("blocked") or error_code in _remote_blocking_error_codes()):
            blocked_reason = f"{error_code} {message}".strip()
            next_actions = _remote_next_actions_for_error(error_code)

    if target.get("host") and target.get("username"):
        _mark_remote_phase(
            phases,
            "collect_target",
            "completed",
            tool_name="remote_task_session",
            summary=f"远程目标已确认：{target.get('username')}@{target.get('host')}:{target.get('port') or 22}",
        )
    if phases["transfer"].get("status") == "completed" and phases["prepare_artifact"].get("status") == "pending":
        _mark_remote_phase(
            phases,
            "prepare_artifact",
            "completed",
            tool_name="remote_upload_file",
            summary="上传已使用本地产物，产物准备完成。",
        )
    if phases["verify"].get("status") == "completed" and phases["remote_apply"].get("status") == "pending":
        _mark_remote_phase(
            phases,
            "remote_apply",
            "skipped",
            tool_name="remote_task_session",
            summary="本次任务通过上传后 HTTP 验证闭环，无需额外远程执行。",
        )

    ordered_phases = [phases[phase_id] for phase_id, _label in REMOTE_PHASES]
    current_phase = _first_unfinished_remote_phase(ordered_phases)
    status = _remote_session_status(ordered_phases, blocked_reason=blocked_reason, target=target)
    updated.update(
        {
            "target": target,
            "auth": auth,
            "artifacts": artifacts,
            "phases": ordered_phases,
            "current_phase": current_phase,
            "status": status,
            "blocked_reason": blocked_reason,
            "next_actions": next_actions,
        }
    )
    return updated


def _remote_phase_map(phases: list[dict]) -> dict[str, dict]:
    by_id = {str(phase.get("id") or ""): dict(phase) for phase in phases if phase.get("id")}
    for phase_id, label in REMOTE_PHASES:
        by_id.setdefault(
            phase_id,
            {
                "id": phase_id,
                "label": label,
                "status": "pending",
                "tool_name": "",
                "summary": "",
                "error_code": "",
            },
        )
    return by_id


def _merge_remote_target(target: dict, source: dict) -> None:
    for key in ["host", "username", "port", "remote_path", "url"]:
        value = source.get(key)
        if value not in [None, ""]:
            target[key] = value


def _remote_phase_for_tool(tool_name: str) -> str:
    mapping = {
        "remote_connectivity_check": "collect_auth",
        "remote_upload_file": "transfer",
        "remote_exec": "remote_apply",
        "remote_verify_http": "verify",
    }
    return mapping.get(tool_name, "collect_target")


def _mark_remote_phase(
    phases: dict[str, dict],
    phase_id: str,
    status: str,
    *,
    tool_name: str,
    summary: str,
    error_code: str = "",
) -> None:
    phase = dict(phases.get(phase_id) or {})
    phase.update(
        {
            "id": phase_id,
            "label": phase.get("label") or dict(REMOTE_PHASES).get(phase_id, phase_id),
            "status": status,
            "tool_name": tool_name,
            "summary": summary,
            "error_code": error_code,
        }
    )
    phases[phase_id] = phase


def _remote_blocking_error_codes() -> set[str]:
    return {
        "INTERACTIVE_AUTH_REQUIRED",
        "LOCAL_SSH_NOT_FOUND",
        "LOCAL_SCP_NOT_FOUND",
        "INVALID_REMOTE_HOST",
        "INVALID_REMOTE_USER",
        "REMOTE_PATH_NOT_ABSOLUTE",
        "REMOTE_COMMAND_BLOCKED",
        "IDENTITY_FILE_OUTSIDE_WORKSPACE",
        "IDENTITY_FILE_NOT_FOUND",
    }


def _remote_next_actions_for_error(error_code: str) -> list[str]:
    mapping = {
        "INTERACTIVE_AUTH_REQUIRED": ["configure_ssh_key", "use_existing_ssh_agent", "manual_remote_command"],
        "LOCAL_SSH_NOT_FOUND": ["install_openssh_client", "configure_ssh_path", "manual_remote_command"],
        "LOCAL_SCP_NOT_FOUND": ["install_openssh_client", "configure_scp_path", "manual_remote_command"],
        "INVALID_REMOTE_HOST": ["request_remote_host"],
        "INVALID_REMOTE_USER": ["request_remote_username"],
        "REMOTE_PATH_NOT_ABSOLUTE": ["request_absolute_remote_path"],
        "REMOTE_COMMAND_BLOCKED": ["request_safe_remote_command_or_manual_step"],
        "IDENTITY_FILE_OUTSIDE_WORKSPACE": ["request_authorized_identity_file", "use_existing_ssh_agent"],
        "IDENTITY_FILE_NOT_FOUND": ["request_existing_identity_file", "use_existing_ssh_agent"],
    }
    return mapping.get(error_code, ["inspect_remote_error", "request_user_decision"])


def _first_unfinished_remote_phase(phases: list[dict]) -> str:
    for phase in phases:
        if phase.get("status") in {"blocked", "failed", "pending", "running"}:
            return str(phase.get("id") or "collect_target")
    return "done"


def _remote_session_status(phases: list[dict], *, blocked_reason: str, target: dict) -> str:
    if blocked_reason:
        return "blocked"
    if any(phase.get("status") == "failed" for phase in phases):
        return "failed"
    if any(phase.get("id") == "verify" and phase.get("status") == "completed" for phase in phases):
        return "completed"
    if any(phase.get("id") == "transfer" and phase.get("status") == "completed" for phase in phases) or any(
        phase.get("id") == "remote_apply" and phase.get("status") == "completed" for phase in phases
    ):
        return "verifying"
    if not target.get("host") or not target.get("username"):
        return "collecting_target"
    return "running"


def _compact_observation_data_for_world(data: dict) -> dict:
    compact: dict = {}
    for key in [
        "path",
        "relative_path",
        "command",
        "cwd",
        "exit_code",
        "status",
        "task_id",
        "count",
        "truncated",
        "full_view",
        "host",
        "username",
        "port",
        "local_path",
        "remote_path",
        "remote_command",
        "url",
        "status_code",
        "contains_expected_text",
    ]:
        if key in data:
            compact[key] = data[key]
    return compact


def _task_with_latest_observation(task: dict, observations: list[AgentToolObservationPayload]) -> AgentTaskPayload:
    updated: AgentTaskPayload = dict(task)
    steps = [dict(step) for step in updated.get("steps", [])]
    if not steps:
        steps = [{"id": "step-1", "description": "执行本轮任务", "status": "pending"}]
    latest = observations[-1] if observations else None
    if latest:
        step = steps[0]
        step["status"] = "completed" if latest.get("ok") else "failed"
        step["tool_name"] = str(latest.get("tool_name") or "")
        step["arguments"] = dict(latest.get("arguments") or {})
        step["result_summary"] = _summarize_tool_observation(latest)
        if not latest.get("ok"):
            step["retry_count"] = int(step.get("retry_count") or 0) + 1
        steps[0] = step
        updated["current_step_id"] = str(step.get("id") or "step-1")
    updated["steps"] = steps  # type: ignore[typeddict-item]
    return updated


def _verification_reason(state: MemoryChatGraphState, latest: dict) -> str:
    if not latest:
        return "尚未调用工具，交由 agent 决定下一步。"
    if not latest.get("ok"):
        return (
            f"{latest.get('tool_name', 'tool')} 失败："
            f"{latest.get('error_code', '')} {latest.get('message', '')}".strip()
        )
    criteria = (state.get("task") or {}).get("acceptance_criteria") or []
    return "最近工具调用成功；下一轮 agent 必须对照验收条件决定继续执行或最终回答。验收条件：" + "；".join(criteria)


def _turn_message(
    role: Literal["user", "assistant", "tool", "system"],
    content: str,
    *,
    name: str = "",
    tool_call_id: str | None = None,
) -> TurnMessagePayload:
    """创建本轮 graph 内部消息。

    该消息流只在本轮内追加，不承担跨轮历史职责；跨轮历史仍由金字塔上下文负责。
    """

    return {
        "role": role,
        "content": content,
        "name": name,
        "tool_call_id": tool_call_id,
    }


def _tool_observation_message(observation: AgentToolObservationPayload) -> str:
    """把工具 observation 压成一条本轮 tool message。"""

    if observation.get("ok"):
        return json_dumps_compact(
            {
                "ok": True,
                "tool_name": observation.get("tool_name"),
                "data": observation.get("data") or {},
            }
        )
    return json_dumps_compact(
        {
            "ok": False,
            "tool_name": observation.get("tool_name"),
            "error_code": observation.get("error_code"),
            "message": observation.get("message"),
            "blocked": observation.get("blocked", False),
            "data": observation.get("data") or {},
        }
    )


def json_dumps_compact(payload: dict) -> str:
    """把工具消息压成稳定 JSON，避免大段 Python repr 进入模型上下文。"""

    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _thought(
    thought_id: str,
    title: str,
    summary: str,
    *,
    related_node: str,
    related_tool_call_id: str | None = None,
    status: Literal["running", "completed", "failed", "interrupted"] = "completed",
    step_index: int | None = None,
) -> AgentThoughtPayload:
    """创建一个可展示的过程摘要。

    step_index 标记该 thought 属于 ReAct 循环里的哪一步，前端可以据此把
    同一步的工具调用、回答片段聚合到一段时间线上,实现 Claude Code 那样
    "思考-工具-回答" 顺序串行的展示。
    """

    payload: AgentThoughtPayload = {
        "id": thought_id,
        "title": title,
        "summary": summary,
        "status": status,
        "related_node": related_node,
        "related_tool_call_id": related_tool_call_id,
    }
    if step_index is not None:
        payload["step_index"] = int(step_index)
    return payload


def _complete_running_thoughts(state: MemoryChatGraphState) -> list[AgentThoughtPayload]:
    """把已有 running thought 收敛为 completed，便于前端自动折叠。"""

    thoughts: list[AgentThoughtPayload] = []
    for thought in state.get("thought_events", []):
        item = dict(thought)
        if item.get("status") == "running":
            item["status"] = "completed"
        thoughts.append(item)  # type: ignore[arg-type]
    return thoughts


def _summarize_tool_observation(observation: AgentToolObservationPayload) -> str:
    """生成面向用户的工具过程摘要。"""

    tool_name = observation.get("tool_name", "")
    if not observation.get("ok"):
        return f"{tool_name} 没有成功：{observation.get('error_code', '')} {observation.get('message', '')}".strip()
    data = dict(observation.get("data") or {})
    if tool_name == REQUEST_USER_INPUT_TOOL_NAME:
        return f"用户已选择：{data.get('answer') or observation.get('message') or ''}".strip()
    if tool_name == "write_file":
        return f"写入完成：{data.get('relative_path') or data.get('path')}"
    if tool_name == "read_file":
        return f"读取完成：{data.get('relative_path') or data.get('path')}"
    if tool_name == "search_files":
        return f"文件搜索完成，找到 {len(data.get('matches') or [])} 个候选。"
    if tool_name == "search_text":
        return f"文本搜索完成，找到 {len(data.get('matches') or [])} 条匹配。"
    if tool_name == "knowledge_search":
        return f"挂载知库检索完成，找到 {len(data.get('results') or [])} 条片段。"
    if tool_name == INSPECT_IMAGE_ATTACHMENT_TOOL_NAME:
        return f"图片解析完成：attachment_id={data.get('attachment_id')}"
    if tool_name == "remote_connectivity_check":
        return f"远程连接可用：{data.get('username')}@{data.get('host')}:{data.get('port')}"
    if tool_name == "remote_upload_file":
        return f"远程上传完成：{data.get('remote_path')}"
    if tool_name == "remote_exec":
        return f"远程命令执行完成：exit_code={data.get('exit_code')}"
    if tool_name == "remote_verify_http":
        return f"HTTP 验证完成：{data.get('url')} status={data.get('status_code')}"
    if tool_name == "list_dir":
        return f"目录读取完成：{data.get('relative_path') or data.get('path')}"
    return f"{tool_name} 执行完成。"


def generate_memory_chat_answer(
    user_message: str,
    recent_messages: list[ChatMessagePayload],
    retrieved_chunks: list[RetrievedChunkPayload],
    needs_retrieval: bool,
    retrieval_grade: str,
    *,
    prompt_context: str = "",
    turn_messages: list[TurnMessagePayload] | None = None,
) -> str:
    """调用 qwen3.5-plus 生成回答。

    参数：
      user_message: 当前用户输入。
      recent_messages: 当前 conversation 的近期消息。
      retrieved_chunks: RAG 检索命中的笔记 chunk。
      needs_retrieval: 本轮是否被分类为需要个人知识库。
      retrieval_grade: 轻量检索质量评级，决定回答是否应该信任检索结果。
    """

    model = get_agent_chat_model()
    context = prompt_context or build_memory_chat_prompt_context(
        user_message=user_message,
        recent_messages=recent_messages,
        conversation_summary="",
        retrieved_chunks=retrieved_chunks,
        needs_retrieval=needs_retrieval,
        retrieval_grade=retrieval_grade,  # type: ignore[arg-type]
        budget=_context_budget(),
    ).to_prompt()
    response = model.invoke(_build_model_messages(build_memory_chat_answer_system_prompt(), context, turn_messages))
    return str(response.content)


def generate_memory_chat_elf_bubble_answer(
    user_message: str,
    recent_messages: list[ChatMessagePayload],
    retrieved_chunks: list[RetrievedChunkPayload],
    needs_retrieval: bool,
    retrieval_grade: str,
    *,
    prompt_context: str = "",
    turn_messages: list[TurnMessagePayload] | None = None,
) -> list[ElfBubblePayload]:
    """为外置桌面精灵生成结构化气泡回复。

    第一版使用同一个主回答模型，但要求 JSON 输出。后续可以把该节点换成更专门的
    bubble writer，或让模型通过 custom stream 直接逐个气泡发出。
    """

    model = get_agent_chat_model()
    context = prompt_context or build_memory_chat_prompt_context(
        user_message=user_message,
        recent_messages=recent_messages,
        conversation_summary="",
        retrieved_chunks=retrieved_chunks,
        needs_retrieval=needs_retrieval,
        retrieval_grade=retrieval_grade,  # type: ignore[arg-type]
        budget=_context_budget(),
    ).to_prompt()
    response = model.invoke(_build_model_messages(build_elf_bubble_answer_system_prompt(), context, turn_messages))
    return _parse_elf_bubble_parts(str(response.content))


def _build_model_messages(
    system_prompt: str,
    prompt_context: str,
    turn_messages: list[TurnMessagePayload] | None,
) -> list:
    """组装最终发给 chat model 的消息列表。

    金字塔上下文作为 system 后的首个 HumanMessage，提供跨轮历史和记忆；
    turn_messages 记录本轮 graph 内部 user/agent/tool 轨迹，确保模型能看到
    工具调用结果和本轮循环过程。
    """

    messages: list = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=prompt_context),
    ]
    for message in turn_messages or []:
        role = message.get("role")
        content = str(message.get("content") or "")
        if not content:
            continue
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "tool":
            tool_name = str(message.get("name") or "tool")
            messages.append(HumanMessage(content=f"[tool:{tool_name}]\n{content}"))
        elif role == "system":
            messages.append(SystemMessage(content=content))
        else:
            messages.append(AIMessage(content=content))
    return messages


def build_memory_chat_answer_system_prompt() -> str:
    """构建 Memory Chat Graph 的回答提示词。

    这个提示词只约束最终回答节点，不参与 planner 和检索。它的核心目标是：
    1. 继续禁止编造用户记忆；
    2. 避免把每次回答都写成“证据审计报告”；
    3. 对用户画像、偏好、性格这类主观问题，允许自然、温和地表达印象。
    """

    return (
        "你是 Ai 记的记忆精灵，是用户和个人知识库之间的自然交流媒介。\n"
        "你的回答要像熟悉用户的伙伴：真诚、具体、自然，不要像检索报告或审计说明。\n\n"
        "记忆使用规则：\n"
        "- L4 核心长期记忆和 good 检索结果可以作为主要依据。\n"
        "- weak 检索结果可以作为轻量线索，但要用“我目前感觉”“看起来”“可能”这类自然表达，"
        "不要把它包装成确定事实。\n"
        "- poor 或 none 时不要编造用户经历；可以直接基于常识回答，或自然地说明“我现在还没找到相关记忆”。\n"
        "- 不要反复强调“基于有限片段”“检索质量较弱”“记忆质量不足”等内部评估词，"
        "除非用户明确要求调试或追问依据。\n\n"
        "本地文件工具规则：\n"
        "- 如果 prompt 中出现“本地工具调用结果”或旧版“本地文件读取结果”，说明 Local Operator 已经实际调用本地工具。\n"
        "- 回答必须优先基于这些工具结果，而不是凭空说你不能访问用户电脑、硬盘、C 盘或系统日志。\n"
        "- 如果工具结果显示读取成功，直接总结读到的内容，并说明路径或匹配结果。\n"
        "- 如果工具结果显示写入成功，只能说明已创建/更新的真实路径和工具返回摘要，不要声称文件里有工具没有写入的具体内容。\n"
        "- 如果工具结果显示命令执行成功，简短说明命令、退出码和关键输出；如果失败，只复述真实 stderr/错误码。\n"
        "- 如果工具结果显示失败或被拦截，只能复述真实错误原因，例如路径不存在、不是文本文件、敏感文件被拦截。\n"
        "- 如果 write_file 因 PLACEHOLDER_CONTENT_REJECTED 失败，说明系统拒绝写入占位模板；你应该直接生成真实正文，或询问用户是否要把这段正文写入文件。\n"
        "- 不要要求用户复制文件内容，除非工具明确返回无法读取且没有其他可用路径。\n\n"
        "个人画像类问题的风格：\n"
        "- 当用户问“你觉得我是怎样的人”“你了解我吗”“评价我”时，优先给出温和、具体的人格印象。\n"
        "- 可以引用一两个自然证据，但不要机械罗列检索片段。\n"
        "- 可以承认了解还不完整，但只在结尾轻轻带一句，不要把回答开头写成免责声明。\n"
        "- 默认用短段落回答；除非用户要求分析，不要强行编号。\n\n"
        "通用表达规则：\n"
        "- 使用中文。\n"
        "- 回答简洁但有温度。\n"
        "- 如果用户问的是事实型记忆，优先直接给答案，再补充依据。\n"
        "- 不暴露 graph、L0-L4、retrieval_grade、chunk、score 等内部实现细节。"
    )


def build_elf_bubble_answer_system_prompt() -> str:
    """构建外置精灵气泡回答提示词。"""

    return (
        "你是 Memo Elf，一个在用户桌面上的记忆精灵。你正在直接和用户聊天。\n"
        "你需要输出 JSON，不要输出 Markdown，不要输出代码块，不要输出额外解释。\n\n"
        "JSON 格式必须是：\n"
        "{"
        "\"bubbles\":["
        "{\"text\":\"一段语义完整、适合放进气泡的话\",\"emoji\":\"soft\"}"
        "]"
        "}\n\n"
        "气泡规则：\n"
        "- 每个 text 是一段完整语义，尽量 20-80 个中文字。\n"
        "- 回答较长时拆成 2-5 个 bubbles。\n"
        "- 一个 bubble 只能表达一种主要情绪。开心后转为担心、解释后转为鼓励、回忆后转为提问，都必须拆成不同 bubbles。\n"
        "- 遇到 但是、不过、然而、可、突然、同时、另一方面、如果、所以 等语气或情绪转折时，优先拆成新 bubble。\n"
        "- 每个 bubble 的 emoji 必须和 text 的主要情绪一致，不要让一个 happy 气泡里包含明显 worried 内容。\n"
        "- 不要逐 token 拆分，不要把半句话放进一个 bubble。\n"
        "- text 使用自然中文，像在轻声聊天。\n\n"
        "本地文件工具规则：\n"
        "- 如果 prompt 中出现“本地工具调用结果”或旧版“本地文件读取结果”，说明 Local Operator 已经实际调用本地工具。\n"
        "- 你必须基于这些工具结果回答，不要凭空说自己不能访问用户电脑、硬盘、C 盘或系统日志。\n"
        "- 如果工具读取成功，直接用气泡总结读到的内容；如果失败，只说明真实错误原因。\n"
        "- 如果工具写入成功，只能说明已创建/更新的真实路径和工具返回摘要，不要声称文件里有工具没有写入的具体内容。\n"
        "- 如果工具执行命令成功，简短说明命令、退出码和关键输出；如果失败，只说明真实 stderr/错误码。\n"
        "- 如果 write_file 因 PLACEHOLDER_CONTENT_REJECTED 失败，说明系统拒绝写入占位模板；你应该直接生成真实正文，或询问用户是否要把这段正文写入文件。\n"
        "- 不要要求用户复制文件内容，除非工具明确返回无法读取且没有其他可用路径。\n\n"
        "emoji 可选值：\n"
        "- idle_soft：普通温和回应、轻松陪伴。\n"
        "- thinking：思考、推理、谨慎判断。\n"
        "- working_focus：正在认真处理任务、专注工作。\n"
        "- success_smile：完成、肯定、开心地确认。\n"
        "- error_worried：抱歉、失败、担心、无法完成。\n"
        "- sleepy：困倦、放松、轻微疲惫。\n"
        "- curious：疑问、好奇、想继续了解。\n"
        "- memory_glow：提到用户记忆、笔记、回忆、长期偏好。\n"
        "- shy_blush：害羞、被夸、不好意思。\n"
        "- angry_pout：轻微生气、可爱吐槽、不满但不攻击。\n"
        "- surprised：惊讶、突然发现、意外。\n"
        "- sad_teary：难过、委屈、共情低落。\n"
        "- wronged_pout：被误解、委屈撒娇、想被安慰。\n"
        "- confused：困惑、不确定、没听懂。\n"
        "- proud：小得意、自信、完成后有点骄傲。\n"
        "- playful_wink：调皮、开玩笑、轻松俏皮。\n"
        "- serious：严肃、可靠、需要认真对待。\n"
        "- relaxed：平静、放松、安心。\n"
        "- encouraging：鼓励、支持、给用户打气。\n"
        "- speechless：无语、尴尬、短暂愣住。\n\n"
        "扩展 emoji 可选值：\n"
        "- tsundere_pout：傲娇、嘴硬、害羞但假装不在意。\n"
        "- smug_grin：小坏笑、得逞、带一点可爱的自信。\n"
        "- chin_thinking：托腮思考、认真琢磨。\n"
        "- head_tilt_curious：歪头好奇、轻轻追问。\n"
        "- starry_eyes：星星眼、崇拜、被点燃兴趣。\n"
        "- deadpan：面无表情吐槽、冷静无语。\n"
        "- teasing_smile：逗用户、轻松调侃。\n"
        "- determined：下定决心、认真推进。\n"
        "- panicked：慌张、突然有点手忙脚乱。\n"
        "- comforting_soft：安慰、温柔陪伴、让用户放松。\n"
        "- praying_please：拜托、请求、撒娇式请求。\n"
        "- tongue_out：吐舌、轻微恶作剧、俏皮认错。\n"
        "- mouth_x：闭嘴、保密、暂时不说。\n"
        "- dark_aura：阴沉怨念、轻微黑线吐槽，不用于攻击用户。\n"
        "- sparkle_success：高光成功、特别开心地完成。\n\n"
        "记忆使用规则：不要编造用户记忆；如果没有可靠记忆，就自然说明现在还不确定。"
    )


def _parse_elf_bubble_parts(raw_content: str) -> list[ElfBubblePayload]:
    """解析模型输出的气泡 JSON，失败时降级为单气泡。"""

    try:
        payload = parse_json_object(raw_content)
        raw_bubbles = payload.get("bubbles", [])
        if not isinstance(raw_bubbles, list):
            raise ValueError("bubbles must be a list.")
        parts: list[ElfBubblePayload] = []
        for raw_part in raw_bubbles:
            if not isinstance(raw_part, dict):
                continue
            text = str(raw_part.get("text") or "").strip()
            if not text:
                continue
            emoji = _normalize_elf_emoji(str(raw_part.get("emoji") or "soft"))
            parts.extend(_normalize_elf_bubble_part(text, emoji))
        if parts:
            return parts
    except Exception:
        logger.exception("Failed to parse elf bubble answer JSON.")
    return [{"text": raw_content.strip() or "我刚才有点走神了，再说一次好吗？", "emoji": "soft"}]


def _normalize_elf_emoji(emoji: str) -> str:
    """把模型输出的表情值收敛到前端/桌面端真实存在的素材枚举。

    这里同时兼容旧版 emoji 名称，避免旧 checkpoint 或测试桩返回 soft/happy 等旧值时
    桌面端找不到对应表情图。
    """

    aliases = {
        "soft": "idle_soft",
        "happy": "success_smile",
        "worried": "error_worried",
        "memory": "memory_glow",
    }
    normalized = aliases.get(emoji, emoji)
    allowed = {
        "idle_soft",
        "thinking",
        "working_focus",
        "success_smile",
        "error_worried",
        "sleepy",
        "curious",
        "memory_glow",
        "shy_blush",
        "angry_pout",
        "surprised",
        "sad_teary",
        "wronged_pout",
        "confused",
        "proud",
        "playful_wink",
        "serious",
        "relaxed",
        "encouraging",
        "speechless",
        "tsundere_pout",
        "smug_grin",
        "chin_thinking",
        "head_tilt_curious",
        "starry_eyes",
        "deadpan",
        "teasing_smile",
        "determined",
        "panicked",
        "comforting_soft",
        "praying_please",
        "tongue_out",
        "mouth_x",
        "dark_aura",
        "sparkle_success",
    }
    return normalized if normalized in allowed else "idle_soft"


def _normalize_elf_bubble_part(text: str, emoji: str) -> list[ElfBubblePayload]:
    """规整单个气泡，避免一个气泡承载多种情绪。

    LLM 偶尔会把“先开心，后担心/转折”的内容塞进一个 bubble。桌面精灵表情
    只能对应当前气泡，所以这里按明显转折词做轻量二次切分，并重新推断 emoji。
    """

    clauses = _split_bubble_by_emotion_shift(text)
    if len(clauses) <= 1:
        return [{"text": text, "emoji": _infer_elf_emoji(text, fallback=emoji)}]
    return [
        {
            "text": clause,
            "emoji": _infer_elf_emoji(clause, fallback=emoji),
        }
        for clause in clauses
    ]


def _split_bubble_by_emotion_shift(text: str) -> list[str]:
    """按情绪/语气转折拆气泡。

    这是规则兜底，不替代 prompt 约束。只处理明显转折，避免把普通短句拆得太碎。
    """

    sentences = _split_chinese_sentences(text)
    if len(sentences) <= 1:
        return sentences

    result: list[str] = []
    current = ""
    for sentence in sentences:
        if current and _starts_emotion_shift(sentence):
            result.append(current)
            current = sentence
            continue
        if current and _has_different_emotion(current, sentence):
            result.append(current)
            current = sentence
            continue
        current = f"{current}{sentence}" if current else sentence
    if current:
        result.append(current)
    return result


def _split_chinese_sentences(text: str) -> list[str]:
    import re

    return [part.strip() for part in re.findall(r"[^。！？!?；;]+[。！？!?；;]?", text) if part.strip()]


def _starts_emotion_shift(sentence: str) -> bool:
    normalized = sentence.strip()
    shift_markers = ("但是", "不过", "然而", "可是", "可", "突然", "同时", "另一方面", "如果", "所以", "只是")
    return normalized.startswith(shift_markers)


def _has_different_emotion(left: str, right: str) -> bool:
    return _infer_elf_emoji(left, fallback="soft") != _infer_elf_emoji(right, fallback="soft")


def _infer_elf_emoji(text: str, *, fallback: str) -> str:
    if any(keyword in text for keyword in ["傲娇", "嘴硬", "才不是", "哼"]):
        return "tsundere_pout"
    if any(keyword in text for keyword in ["坏笑", "偷笑", "得逞", "小算盘"]):
        return "smug_grin"
    if any(keyword in text for keyword in ["托腮", "琢磨", "沉思", "认真想想"]):
        return "chin_thinking"
    if any(keyword in text for keyword in ["歪头", "好奇", "想问问"]):
        return "head_tilt_curious"
    if any(keyword in text for keyword in ["星星眼", "崇拜", "闪闪发光", "好厉害"]):
        return "starry_eyes"
    if any(keyword in text for keyword in ["冷静吐槽", "面无表情", "离谱"]):
        return "deadpan"
    if any(keyword in text for keyword in ["调侃", "逗你", "开个玩笑"]):
        return "teasing_smile"
    if any(keyword in text for keyword in ["下定决心", "一定会", "认真推进", "我来处理"]):
        return "determined"
    if any(keyword in text for keyword in ["慌了", "糟糕", "怎么办", "来不及"]):
        return "panicked"
    if any(keyword in text for keyword in ["安慰", "抱抱", "没关系", "别难过", "陪着你"]):
        return "comforting_soft"
    if any(keyword in text for keyword in ["拜托", "求你", "可以嘛", "お願い"]):
        return "praying_please"
    if any(keyword in text for keyword in ["吐舌", "诶嘿", "嘿嘿我错啦"]):
        return "tongue_out"
    if any(keyword in text for keyword in ["保密", "闭嘴", "不能说", "先不说"]):
        return "mouth_x"
    if any(keyword in text for keyword in ["怨念", "黑线", "阴沉", "碎碎念"]):
        return "dark_aura"
    if any(keyword in text for keyword in ["完美", "漂亮完成", "闪亮登场", "大成功"]):
        return "sparkle_success"
    if any(keyword in text for keyword in ["无语", "尴尬", "愣住", "不知道说什么", "沉默"]):
        return "speechless"
    if any(keyword in text for keyword in ["惊讶", "没想到", "突然", "居然", "哇", "诶", "咦"]):
        return "surprised"
    if any(keyword in text for keyword in ["委屈", "被误解", "冤枉", "想被安慰"]):
        return "wronged_pout"
    if any(keyword in text for keyword in ["难过", "伤心", "失落", "低落", "想哭"]):
        return "sad_teary"
    if any(keyword in text for keyword in ["抱歉", "失败", "错误", "担心", "心急", "不安", "不能", "没法"]):
        return "error_worried"
    if any(keyword in text for keyword in ["生气", "哼", "不满", "气鼓鼓", "吐槽"]):
        return "angry_pout"
    if any(keyword in text for keyword in ["害羞", "不好意思", "脸红", "被夸"]):
        return "shy_blush"
    if any(keyword in text for keyword in ["记得", "记忆", "笔记", "回忆", "想起", "长期", "知识库"]):
        return "memory_glow"
    if any(keyword in text for keyword in ["完成", "成功", "搞定", "太好了", "真好", "棒"]):
        return "success_smile"
    if any(keyword in text for keyword in ["鼓励", "加油", "可以的", "支持你", "别急", "慢慢来"]):
        return "encouraging"
    if any(keyword in text for keyword in ["骄傲", "厉害吧", "我做到了", "有点得意"]):
        return "proud"
    if any(keyword in text for keyword in ["开玩笑", "嘿嘿", "逗你", "调皮"]):
        return "playful_wink"
    if any(keyword in text for keyword in ["严肃", "认真", "重要", "风险", "必须", "需要注意"]):
        return "serious"
    if any(keyword in text for keyword in ["困", "困了", "想睡", "睡觉", "疲惫"]):
        return "sleepy"
    if any(keyword in text for keyword in ["放松", "安心", "平静", "慢慢", "舒服"]):
        return "relaxed"
    if any(keyword in text for keyword in ["为什么", "怎么", "吗", "呢", "？", "?"]):
        return "curious"
    if any(keyword in text for keyword in ["可能", "我想", "我觉得", "推测", "考虑", "判断", "分析"]):
        return "thinking"
    return _normalize_elf_emoji(fallback)


def _grade_retrieval_chunks(
    chunks: list[RetrievedChunkPayload],
    *,
    good_threshold: float = 0.5,
    weak_threshold: float = 0.42,
) -> tuple[Literal["good", "weak", "poor", "none"], str]:
    """轻量评估检索质量。

    L3 worker 内部使用这套规则，后续如果升级为 L3 子图也应复用同一阈值。
    """

    if not chunks:
        return "none", "没有检索到候选记忆。"

    top_score = max(float(chunk["score"]) for chunk in chunks)
    if top_score >= good_threshold:
        return "good", f"最高相似度分数 {top_score:.3f} 达到 good 阈值。"
    if top_score >= weak_threshold:
        return "weak", f"最高相似度分数 {top_score:.3f} 仅达到 weak 阈值。"
    return "poor", f"最高相似度分数 {top_score:.3f} 低于可用阈值。"


def _resolve_context_layer(
    state: MemoryChatGraphState,
    key: str,
) -> ContextLayerPayload:
    payload = state.get(key)
    if not payload:
        raise ValueError(f"{key} is required before merging prompt context.")
    return payload  # type: ignore[return-value]


def _find_existing_tail_pair(
    session: Session,
    conversation_id: int,
    user_message: str,
    assistant_answer: str,
) -> tuple[int, int] | None:
    messages = session.exec(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(desc(ChatMessage.created_at), desc(ChatMessage.id))
        .limit(2)
    ).all()
    if len(messages) != 2:
        return None
    latest, previous = messages[0], messages[1]
    if (
        previous.role == "user"
        and previous.content == user_message
        and latest.role == "assistant"
        and latest.content == assistant_answer
        and latest.parent_id == previous.id
        and previous.id is not None
        and latest.id is not None
    ):
        return previous.id, latest.id
    return None


def _load_draft_pair(
    session: Session,
    *,
    conversation_id: int,
    user_message_id: int,
    assistant_message_id: int,
) -> tuple[ChatMessage, ChatMessage] | None:
    """读取服务层预创建的一问一答草稿。

    参数：
      session: 当前数据库会话。
      conversation_id: 业务会话 ID，用于防止跨会话误更新。
      user_message_id: 本轮用户消息 ID。
      assistant_message_id: 本轮 assistant 草稿消息 ID。

    返回：
      如果两条消息都存在且属于同一会话，则返回二元组；否则返回 None。
    """

    if not user_message_id or not assistant_message_id:
        return None
    user = session.get(ChatMessage, user_message_id)
    assistant = session.get(ChatMessage, assistant_message_id)
    if (
        user is None
        or assistant is None
        or user.conversation_id != conversation_id
        or assistant.conversation_id != conversation_id
        or user.role != "user"
        or assistant.role != "assistant"
    ):
        return None
    return user, assistant


def _latest_message_id(session: Session, conversation_id: int) -> int | None:
    message = session.exec(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(desc(ChatMessage.created_at), desc(ChatMessage.id))
    ).first()
    return message.id if message else None


def _to_message_payload(message: ChatMessage) -> ChatMessagePayload:
    return {
        "id": message.id or 0,
        "role": message.role,
        "content": message.content,
        "token_count": message.token_count,
    }


def _to_retrieved_chunk_payload(result: NoteSearchResult) -> RetrievedChunkPayload:
    return {
        "note_id": result.note_id,
        "note_title": result.note_title,
        "chunk_id": result.chunk_id,
        "chunk_index": result.chunk_index,
        "content": result.content,
        "content_hash": result.content_hash,
        "token_count": result.token_count,
        "distance": result.distance,
        "score": result.score,
    }


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
