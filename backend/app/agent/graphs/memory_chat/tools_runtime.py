from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager
import logging
from typing import Literal

from langchain_core.tools import StructuredTool
from langgraph.config import get_stream_writer
from pydantic import BaseModel, Field
from sqlmodel import Session

from app.ai.json_utils import parse_json_object
from app.agent.context import ContextBudget
from app.agent.graphs.local_operator.nodes import (
    EXEC_TOOL_NAMES,
    READ_TOOL_NAMES,
    WRITE_TOOL_NAMES,
    _known_existing_paths_from_observations,
    _known_read_files_from_observations,
    _normalize_tool_arguments,
    _observation_to_lines,
)
from app.agent.graphs.memory_chat.attachment_context import _inspect_image_attachment_payload
from app.agent.graphs.memory_chat.knowledge_context import (
    KNOWLEDGE_RETRIEVAL_PROFILES,
    _can_use_knowledge_recall_cache,
    _filter_ready_cached_knowledge_payloads,
    _knowledge_item_to_tool_data,
    _normalize_knowledge_retrieval_profile,
    _select_knowledge_payloads_from_cache,
    _to_knowledge_chunk_payload,
)
from app.agent.graphs.memory_chat.react_agent import _default_local_operator_workspace_roots
from app.agent.graphs.memory_chat.state import (
    AgentThoughtPayload,
    AgentToolActionPayload,
    AgentToolObservationPayload,
    MemoryChatGraphState,
    TurnMessagePayload,
)
from app.agent.graphs.memory_chat.user_input_interrupts import (
    _create_request_user_input_tool,
    _normalize_request_user_input_arguments,
    _run_request_user_input_action,
)
from app.core.config import settings
from app.core.timing import emit_timing
from app.local_operator.policy import LocalOperatorPolicy
from app.local_operator.tools import create_read_tools
from app.rag.chunking.tokenizer import count_tokens
from app.schemas.web_search import WebFetchRequest, WebSearchRequest
from app.services.attachment_service import get_attachment_or_404
from app.services.knowledge_mount_service import list_conversation_knowledge_mounts
from app.services.knowledge_search_service import NEED_KNOWLEDGE_MOUNT
from app.services.web_search_service import WebSearchService


SessionFactory = Callable[[], AbstractContextManager[Session]]
logger = logging.getLogger(__name__)

REQUEST_USER_INPUT_TOOL_NAME = "request_user_input"
USER_INTERRUPT_TOOL_NAMES = {REQUEST_USER_INPUT_TOOL_NAME}
INSPECT_IMAGE_ATTACHMENT_TOOL_NAME = "inspect_image_attachment"
WEB_SEARCH_TOOL_NAME = "web_search"
WEB_FETCH_TOOL_NAME = "web_fetch"


def _nodes_facade():
    from app.agent.graphs.memory_chat import nodes as nodes_facade

    return nodes_facade


def _turn_message(*args, **kwargs):
    return _nodes_facade()._turn_message(*args, **kwargs)


def _tool_observation_message(observation: AgentToolObservationPayload) -> str:
    return _nodes_facade()._tool_observation_message(observation)


def _thought(*args, **kwargs):
    return _nodes_facade()._thought(*args, **kwargs)


def _summarize_tool_observation(observation: AgentToolObservationPayload) -> str:
    return _nodes_facade()._summarize_tool_observation(observation)


def json_dumps_compact(payload: dict) -> str:
    return _nodes_facade().json_dumps_compact(payload)


def _resolve_conversation_id(state: MemoryChatGraphState) -> int:
    conversation_id = state.get("conversation_id")
    if conversation_id is None:
        raise ValueError("conversation_id is required.")
    return int(conversation_id)


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
            | {"knowledge_search", INSPECT_IMAGE_ATTACHMENT_TOOL_NAME, WEB_SEARCH_TOOL_NAME, WEB_FETCH_TOOL_NAME}
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
            elif tool_name == WEB_SEARCH_TOOL_NAME:
                arguments = _normalize_web_search_arguments(raw_arguments)
            elif tool_name == WEB_FETCH_TOOL_NAME:
                arguments = _normalize_web_fetch_arguments(raw_arguments)
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
            update = _nodes_facade()._run_agent_tool_action(
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
        # 又能让模型一次性发出的多个独立读取真正并行（与 通用 coding agent 行为对齐）。
        groups: list[list[tuple[int, dict]]] = []
        current_reads: list[tuple[int, dict]] = []
        for index, tool_call in enumerate(tool_calls):
            tool_name = str(tool_call.get("name") or tool_call.get("tool_name") or "")
            if tool_name in READ_TOOL_NAMES or tool_name in {
                "knowledge_search",
                INSPECT_IMAGE_ATTACHMENT_TOOL_NAME,
                WEB_SEARCH_TOOL_NAME,
                WEB_FETCH_TOOL_NAME,
            }:
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
    tools[WEB_SEARCH_TOOL_NAME] = _create_web_search_tool(state, session_factory=session_factory)
    tools[WEB_FETCH_TOOL_NAME] = _create_web_fetch_tool(state, session_factory=session_factory)
    return tools




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


def _create_web_search_tool(
    state: MemoryChatGraphState,
    *,
    session_factory: SessionFactory,
) -> StructuredTool:
    conversation_id = _resolve_conversation_id(state)

    def web_search(
        query: str,
        max_results: int = 5,
        freshness: str = "any",
        site: str = "",
    ) -> str:
        """Search the public web through the configured AiMemo web search provider."""

        normalized_query = str(query or "").strip()
        if not normalized_query:
            return json_dumps_compact(
                {
                    "ok": False,
                    "tool_name": WEB_SEARCH_TOOL_NAME,
                    "error_code": "INVALID_ARGUMENT",
                    "message": "query 不能为空。",
                    "blocked": True,
                    "data": {"results": []},
                }
            )
        with session_factory() as session:
            service = WebSearchService(session=session, conversation_id=conversation_id)
            response = service.search_and_fetch(
                WebSearchRequest(
                    query=normalized_query,
                    max_results=max(1, min(int(max_results or settings.web_search_max_results), 10)),
                    freshness=freshness if freshness in {"any", "day", "week", "month", "year"} else "any",
                    locale="zh-CN",
                    site=str(site or "").strip(),
                    provider=settings.web_search_provider,
                    model=settings.web_search_model,
                    search_strategy=settings.web_search_strategy,
                )
            )
        return json_dumps_compact(
            {
                "ok": response.ok,
                "tool_name": WEB_SEARCH_TOOL_NAME,
                "error_code": response.error_code,
                "message": response.message,
                "blocked": response.error_code in {"WEB_SEARCH_PRIVATE_QUERY_CONFIRMATION_REQUIRED"},
                "data": response.model_dump(),
            }
        )

    return StructuredTool.from_function(
        func=web_search,
        name=WEB_SEARCH_TOOL_NAME,
        description=(
            "搜索公共互联网资料，适用于用户明确要求联网、最新信息、官网文档、价格、计费、版本、政策、新闻或来源引用。"
            "当本地笔记、挂载知识库或当前附件足以回答时不要调用。"
            "query 必须最小化；不要外发私人笔记原文、未公开代码、本地路径、账号、密钥或聊天隐私。"
            "使用联网结果回答时必须列出来源 URL；涉及价格/政策/API 参数时优先使用 fetched=true 的官方来源。"
        ),
        args_schema=WebSearchToolInput,
    )


def _create_web_fetch_tool(
    state: MemoryChatGraphState,
    *,
    session_factory: SessionFactory,
) -> StructuredTool:
    conversation_id = _resolve_conversation_id(state)

    def web_fetch(url: str, max_chars: int = 12000) -> str:
        """Fetch and extract text from a public http(s) URL with SSRF protection."""

        with session_factory() as session:
            service = WebSearchService(session=session, conversation_id=conversation_id)
            response = service.fetch(WebFetchRequest(url=str(url or "").strip(), max_chars=max_chars))
        return json_dumps_compact(
            {
                "ok": response.ok,
                "tool_name": WEB_FETCH_TOOL_NAME,
                "error_code": response.error_code,
                "message": response.message,
                "blocked": response.error_code == "WEB_FETCH_BLOCKED_URL",
                "data": response.model_dump(),
            }
        )

    return StructuredTool.from_function(
        func=web_fetch,
        name=WEB_FETCH_TOOL_NAME,
        description=(
            "读取并抽取指定公网 http(s) URL 的正文，用于核验 web_search 返回的来源。"
            "不能抓取 localhost、内网地址、file:// 或非 http(s) URL。"
            "需要精确价格、政策、API 参数或官方说明时，应优先 fetch 官方来源后再回答。"
        ),
        args_schema=WebFetchToolInput,
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


class WebSearchToolInput(BaseModel):
    query: str = Field(min_length=1, description="公网搜索 query。必须最小化，不要包含私人笔记原文、密钥、本地路径或未公开代码。")
    max_results: int = Field(default=5, ge=1, le=10, description="最多返回多少条来源。")
    freshness: Literal["any", "day", "week", "month", "year"] = Field(default="any", description="时效范围。")
    site: str = Field(default="", description="可选域名过滤，例如 aliyun.com。")


class WebFetchToolInput(BaseModel):
    url: str = Field(min_length=1, description="要核验的 http(s) URL。")
    max_chars: int = Field(default=12000, ge=100, le=50000, description="最多返回多少正文字符。")


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
            result = _nodes_facade().search_mounted_knowledge(
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


def _normalize_web_search_arguments(arguments: dict) -> dict:
    query = str(
        arguments.get("query")
        or arguments.get("q")
        or arguments.get("search_query")
        or arguments.get("keyword")
        or arguments.get("keywords")
        or ""
    ).strip()
    try:
        max_results = int(arguments.get("max_results") or arguments.get("top_k") or settings.web_search_max_results)
    except (TypeError, ValueError):
        max_results = settings.web_search_max_results
    freshness = str(arguments.get("freshness") or "any").strip().lower()
    if freshness not in {"any", "day", "week", "month", "year"}:
        freshness = "any"
    return {
        "query": query,
        "max_results": max(1, min(max_results, 10)),
        "freshness": freshness,
        "site": str(arguments.get("site") or arguments.get("domain") or "").strip(),
    }


def _normalize_web_fetch_arguments(arguments: dict) -> dict:
    url = str(arguments.get("url") or arguments.get("link") or arguments.get("href") or "").strip()
    try:
        max_chars = int(arguments.get("max_chars") or arguments.get("limit") or 12000)
    except (TypeError, ValueError):
        max_chars = 12000
    return {
        "url": url,
        "max_chars": max(100, min(max_chars, 50000)),
    }




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
        tools[WEB_SEARCH_TOOL_NAME] = _create_web_search_tool(state, session_factory=session_factory)
        tools[WEB_FETCH_TOOL_NAME] = _create_web_fetch_tool(state, session_factory=session_factory)
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


