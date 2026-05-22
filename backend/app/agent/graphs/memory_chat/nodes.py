from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
import json
import logging
from pathlib import Path
import re
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.types import Send
from sqlmodel import Session, desc, select

from app.ai.json_utils import parse_json_object
from app.agent.graphs.local_operator.nodes import (
    READ_TOOL_NAMES,
    WRITE_TOOL_NAMES,
    _expand_planned_actions,
    _extract_path,
    _known_existing_paths_from_observations,
    _llm_plan_tool_action,
    _looks_like_overwrite_request,
    _normalize_tool_arguments,
    _observation_to_lines,
    _rule_plan_action,
)
from app.agent.context import (
    ContextBudget,
    PyramidPromptContext,
    build_core_memory_layer,
    build_current_conversation_window_layer,
    build_current_input_layer,
    build_recent_messages_layer,
    build_retrieved_memory_layer,
    build_summary_layer,
    context_layer_from_payload,
)
from app.agent.graphs.memory_chat.state import (
    AgentThoughtPayload,
    AgentToolActionPayload,
    AgentToolObservationPayload,
    ChatMessagePayload,
    ContextLayerPayload,
    ElfBubblePayload,
    MemoryChatGraphState,
    RetrievedChunkPayload,
    TurnMessagePayload,
)
from app.agent.context import build_memory_chat_prompt_context
from app.agent.model import get_agent_chat_model, get_planner_chat_model
from app.core.config import settings
from app.core.timing import elapsed_ms, emit_timing, now_counter
from app.local_operator.policy import LocalOperatorPolicy
from app.local_operator.tools import create_read_tools
from app.models.chat_message import ChatMessage
from app.models.conversation import Conversation
from app.models.note import utc_now
from app.rag.search import NoteSearchResult, search_notes
from app.rag.chunking.tokenizer import count_tokens
from app.services.long_term_memory_service import list_core_memories


SessionFactory = Callable[[], AbstractContextManager[Session]]
logger = logging.getLogger(__name__)


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
                "context_l0_layer": {},
                "context_l1_layer": {},
                "context_conversation_window_layer": {},
                "context_l2_layer": {},
                "context_l3_layer": {},
                "context_l4_layer": {},
                "prompt_context": "",
                "turn_messages": [
                    {
                        "role": "user",
                        "content": _resolve_user_message(state),
                        "name": "current_user_input",
                        "tool_call_id": None,
                    }
                ],
                "tool_budget": 6,
                "agent_decision": {},
                "planned_tool_actions": [],
                "pending_tool_action": None,
                "tool_policy_result": {},
                "tool_observations": [],
                "tool_observation_context": "",
                "thought_events": [],
                "agent_loop_count": 0,
                "answer_mode": state.get("answer_mode", "text"),
                "assistant_answer": "",
                "elf_bubble_answer_parts": [],
                # 保留服务层预创建的消息 ID，最终 persist_messages 会更新这些草稿消息。
                "user_message_id": int(state.get("user_message_id") or 0),
                "assistant_message_id": int(state.get("assistant_message_id") or 0),
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
        Send("build_l2_summary", state),
        Send("build_l1_recent_messages", state),
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
        layer = build_core_memory_layer(core_memories, ContextBudget())
        return {"context_l4_layer": layer.to_payload()}

    return build_l4_core_memory


def build_l3_retrieved_memory_node(
    session_factory: SessionFactory,
    *,
    planner: RetrievalPlanner | None = None,
    retriever: NoteRetriever = search_notes,
    limit: int = 5,
):
    """构建 L3 RAG 检索记忆层。

    L3 是唯一依赖检索规划的金字塔层。为了让主图变扁，plan/retrieve/grade
    都下放到这个 worker 内部执行；L0/L1/L2/L4 不再等待检索链路。
    """

    def build_l3_retrieved_memory(state: MemoryChatGraphState) -> MemoryChatGraphState:
        user_message = _resolve_user_message(state)
        recent_messages = state.get("recent_messages", [])
        total_started_at = now_counter()
        planner_started_at = now_counter()
        plan = (planner or default_retrieval_planner)(user_message, recent_messages)
        planner_elapsed_ms = elapsed_ms(planner_started_at)

        retrieved_chunks: list[RetrievedChunkPayload] = []
        retrieval_grade: Literal["good", "weak", "poor", "none"] = "none"
        retrieval_grade_reason = "本轮未查询个人知识库。"
        retrieval_query = plan.retrieval_query or user_message
        retriever_elapsed_ms = 0
        grade_elapsed_ms = 0
        if plan.needs_retrieval:
            with session_factory() as session:
                retriever_started_at = now_counter()
                results = retriever(session, query=retrieval_query, limit=limit)
                retriever_elapsed_ms = elapsed_ms(retriever_started_at)
            retrieved_chunks = [_to_retrieved_chunk_payload(result) for result in results]
            grade_started_at = now_counter()
            retrieval_grade, retrieval_grade_reason = _grade_retrieval_chunks(retrieved_chunks)
            grade_elapsed_ms = elapsed_ms(grade_started_at)

        layer_started_at = now_counter()
        layer = build_retrieved_memory_layer(
            retrieved_chunks,
            plan.needs_retrieval,
            retrieval_grade,
            ContextBudget(),
        )
        layer_elapsed_ms = elapsed_ms(layer_started_at)
        retrieval_debug = {
            "planner_ms": planner_elapsed_ms,
            "retriever_ms": retriever_elapsed_ms,
            "grade_ms": grade_elapsed_ms,
            "layer_ms": layer_elapsed_ms,
            "total_ms": elapsed_ms(total_started_at),
            "planner_source": plan.source,
            "needs_retrieval": plan.needs_retrieval,
            "retrieval_query": retrieval_query if plan.needs_retrieval else "",
            "retrieved_count": len(retrieved_chunks),
        }
        logger.info("memory_chat.l3_timing %s", retrieval_debug)
        return {
            "intent": plan.intent,
            "needs_retrieval": plan.needs_retrieval,
            "needs_query_rewrite": plan.needs_query_rewrite,
            "retrieval_query": retrieval_query if plan.needs_retrieval else "",
            "plan_confidence": plan.confidence,
            "retrieval_reason": plan.reason,
            "retrieved_chunks": retrieved_chunks,
            "retrieval_grade": retrieval_grade,
            "retrieval_grade_reason": retrieval_grade_reason,
            "retrieval_debug": retrieval_debug,
            "context_l3_layer": layer.to_payload(),
        }

    return build_l3_retrieved_memory


def build_l2_summary_node():
    """构建 L2 对话摘要层。"""

    def build_l2_summary(state: MemoryChatGraphState) -> MemoryChatGraphState:
        layer = build_summary_layer(state.get("conversation_summary", ""), ContextBudget())
        return {"context_l2_layer": layer.to_payload()}

    return build_l2_summary


def build_l1_recent_messages_node():
    """构建 L1 近期对话窗口层。"""

    def build_l1_recent_messages(state: MemoryChatGraphState) -> MemoryChatGraphState:
        layer = build_recent_messages_layer(state.get("recent_messages", []), ContextBudget())
        return {"context_l1_layer": layer.to_payload()}

    return build_l1_recent_messages


def build_current_conversation_window_node():
    """构建 L1+L0 当前对话窗口层。

    该层专门给 agent_think 和最终回答使用：近期消息与当前输入被渲染成
    一段连续对话，避免模型把“上一轮 assistant 给出的路径/正文”和
    “当前用户确认保存”割裂开。
    """

    def build_current_conversation_window(state: MemoryChatGraphState) -> MemoryChatGraphState:
        layer = build_current_conversation_window_layer(
            state.get("recent_messages", []),
            _resolve_user_message(state),
            ContextBudget(),
        )
        return {"context_conversation_window_layer": layer.to_payload()}

    return build_current_conversation_window


def build_l0_current_input_node():
    """构建 L0 当前输入层。"""

    def build_l0_current_input(state: MemoryChatGraphState) -> MemoryChatGraphState:
        layer = build_current_input_layer(_resolve_user_message(state))
        return {"context_l0_layer": layer.to_payload()}

    return build_l0_current_input


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

    给模型的底层上下文使用 L1+L0 合并后的“当前对话窗口”。单独的 L1/L0
    仍保留在 state/debug 中，方便排查预算裁剪和当前输入。
    """

    def merge_prompt_context(state: MemoryChatGraphState) -> MemoryChatGraphState:
        payloads: list[ContextLayerPayload] = [
            _resolve_context_layer(state, "context_l4_layer"),
            _resolve_context_layer(state, "context_l3_layer"),
            _resolve_context_layer(state, "context_l2_layer"),
            _resolve_context_layer(state, "context_conversation_window_layer"),
        ]
        layers = [context_layer_from_payload(dict(payload)) for payload in payloads]
        context = PyramidPromptContext(layers=layers)
        prompt_context = context.to_prompt()
        return {"prompt_context": prompt_context}

    return merge_prompt_context


def build_agent_think_node(
    planner: Callable[[MemoryChatGraphState], AgentToolActionPayload | None] | None = None,
):
    """主对话 agent 的决策节点。

    参数：
      planner: 测试用注入点。生产环境由 agent_think 自己基于 prompt_context、
        turn_messages 和工具 observation 产出结构化 tool action；Local Operator
        的规则/LLM planner 只作为兜底能力。

    节点职责：
      1. 如果刚执行过工具，把观察结果纳入上下文。
      2. 判断是否继续调用工具，或进入最终回答分支。
      3. 产出 thought_events，给前端/精灵展示可审计过程摘要。
    """

    def agent_think(state: MemoryChatGraphState) -> MemoryChatGraphState:
        loop_count = int(state.get("agent_loop_count") or 0) + 1

        # 如果上一轮 agent_think 已经拆出多个工具动作，优先继续执行队列。
        # 这避免“先 get_file_info，再 write_file”的计划在第一个 observation 后被过早终止。
        if state.get("planned_tool_actions"):
            return {
                "agent_loop_count": loop_count,
                "agent_decision": {"type": "tool_call", "reason": "继续执行已规划的工具队列。"},
                "planned_tool_actions": list(state.get("planned_tool_actions", [])),
                "thought_events": [
                    *_complete_running_thoughts(state),
                    _thought(
                        "agent-think-continue-tool",
                        "继续工具队列",
                        "我还有上一轮已经规划好的工具动作，会继续执行而不是提前回答。",
                        related_node="agent_think",
                    ),
                ],
            }

        if _has_successful_write_observation(state):
            return {
                "agent_loop_count": loop_count,
                "agent_decision": {"type": "final_answer", "reason": "已获得工具观察结果，准备生成最终回答。"},
                "turn_messages": [
                    *state.get("turn_messages", []),
                    _turn_message(
                        "assistant",
                        "已观察到工具结果，准备基于真实结果生成最终回答。",
                        name="agent_think",
                    ),
                ],
                "thought_events": [
                    *_complete_running_thoughts(state),
                    _thought(
                        "agent-think-final",
                        "整理工具结果",
                        "我已经拿到本地工具返回的结果，接下来会基于真实结果回答。",
                        related_node="agent_think",
                    ),
                ],
            }

        action = planner(state) if planner else _think_next_tool_action(state)
        if action is None and state.get("tool_observations"):
            return {
                "agent_loop_count": loop_count,
                "agent_decision": {"type": "final_answer", "reason": "已观察工具结果且无需继续调用工具。"},
                "turn_messages": [
                    *state.get("turn_messages", []),
                    _turn_message(
                        "assistant",
                        "已观察到工具结果，当前不需要继续调用工具，准备基于真实结果回答。",
                        name="agent_think",
                    ),
                ],
                "thought_events": [
                    *_complete_running_thoughts(state),
                    _thought(
                        "agent-think-final",
                        "整理工具结果",
                        "我已经拿到本地工具返回的结果，接下来会基于真实结果回答。",
                        related_node="agent_think",
                    ),
                ],
            }

        if int(state.get("tool_budget") or 0) <= 0:
            return {
                "agent_loop_count": loop_count,
                "agent_decision": {"type": "final_answer", "reason": "工具预算已用尽。"},
                "turn_messages": [
                    *state.get("turn_messages", []),
                    _turn_message(
                        "assistant",
                        "工具调用预算已用尽，停止继续调用工具。",
                        name="agent_think",
                    ),
                ],
                "thought_events": [
                    *_complete_running_thoughts(state),
                    _thought(
                        "agent-think-budget",
                        "停止继续调用工具",
                        "本轮工具调用预算已经用完，我会基于已有上下文回答。",
                        related_node="agent_think",
                    ),
                ],
            }

        if action is None:
            return {
                "agent_loop_count": loop_count,
                "agent_decision": {"type": "final_answer", "reason": "本轮不需要本地工具。"},
                "turn_messages": [
                    *state.get("turn_messages", []),
                    _turn_message(
                        "assistant",
                        "本轮不需要本地工具，直接进入最终回答。",
                        name="agent_think",
                    ),
                ],
                "thought_events": [
                    *_complete_running_thoughts(state),
                    _thought(
                        "agent-think-direct",
                        "直接回答",
                        "这个问题不需要读取或写入本地文件，我会直接结合记忆上下文回答。",
                        related_node="agent_think",
                    ),
                ],
            }

        planned_actions = [_to_agent_tool_action(item, index=index) for index, item in enumerate(_expand_planned_actions(action))]
        return {
            "agent_loop_count": loop_count,
            "agent_decision": {
                "type": "tool_call",
                "reason": action.get("reason", ""),
                "tool_name": action.get("tool_name", ""),
            },
            "planned_tool_actions": planned_actions,
            "turn_messages": [
                *state.get("turn_messages", []),
                _turn_message(
                    "assistant",
                    f"需要调用本地工具 `{action.get('tool_name', '')}`：{action.get('reason', '')}",
                    name="agent_think",
                ),
            ],
            "thought_events": [
                *_complete_running_thoughts(state),
                _thought(
                    "agent-think-tool",
                    "准备调用本地工具",
                    str(action.get("reason") or "我需要先通过本地工具确认信息。"),
                    related_node="agent_think",
                ),
            ],
        }

    return agent_think


def route_after_agent_think(state: MemoryChatGraphState) -> str:
    """agent_think 后的条件边。"""

    decision = state.get("agent_decision") or {}
    return "select_tool" if decision.get("type") == "tool_call" else route_answer_mode(state)


def build_select_tool_node():
    """从主对话工具队列中取出下一次工具调用。"""

    def select_tool(state: MemoryChatGraphState) -> MemoryChatGraphState:
        planned_actions = list(state.get("planned_tool_actions", []))
        if not planned_actions:
            return {"pending_tool_action": None}
        return {
            "pending_tool_action": planned_actions.pop(0),
            "planned_tool_actions": planned_actions,
        }

    return select_tool


def build_check_tool_policy_node():
    """执行前策略检查。

    第一版不接审批 UI，read 直接 allow；write 仍交给工具内部的 workspace、
    敏感文件和 read-before-write 保护。后续这里会接 LangGraph interrupt()。
    """

    def check_tool_policy(state: MemoryChatGraphState) -> MemoryChatGraphState:
        action = state.get("pending_tool_action") or {}
        tool_name = str(action.get("tool_name") or "")
        if tool_name in READ_TOOL_NAMES | WRITE_TOOL_NAMES:
            result = {
                "status": "allow",
                "reason": "工具在当前白名单内，允许进入受控执行节点。",
                "tool_call_id": action.get("tool_call_id"),
            }
        else:
            result = {
                "status": "block",
                "reason": f"未知或未授权工具：{tool_name}",
                "tool_call_id": action.get("tool_call_id"),
            }
        return {
            "tool_policy_result": result,
            "thought_events": [
                *state.get("thought_events", []),
                _thought(
                    f"policy-{action.get('tool_call_id') or 'unknown'}",
                    "检查工具权限",
                    str(result["reason"]),
                    related_node="check_tool_policy",
                    related_tool_call_id=action.get("tool_call_id"),
                ),
            ],
        }

    return check_tool_policy


def route_after_tool_policy(state: MemoryChatGraphState) -> str:
    """策略检查后的条件边。"""

    result = state.get("tool_policy_result") or {}
    if result.get("status") == "allow":
        action = state.get("pending_tool_action") or {}
        return "run_write_tool" if action.get("tool_name") in WRITE_TOOL_NAMES else "run_read_tool"
    return "observe_tool_result"


def build_run_read_tool_node(session_factory: SessionFactory):
    """执行主对话循环中的 read 工具。"""

    def run_read_tool(state: MemoryChatGraphState) -> MemoryChatGraphState:
        return _run_agent_tool_action(state, session_factory=session_factory, allowed_tool_names=READ_TOOL_NAMES)

    return run_read_tool


def build_run_write_tool_node(session_factory: SessionFactory):
    """执行主对话循环中的 write 工具。"""

    def run_write_tool(state: MemoryChatGraphState) -> MemoryChatGraphState:
        return _run_agent_tool_action(state, session_factory=session_factory, allowed_tool_names=WRITE_TOOL_NAMES)

    return run_write_tool


def build_observe_tool_result_node():
    """把工具结果整理为下一轮 agent_think 可消费的上下文。"""

    def observe_tool_result(state: MemoryChatGraphState) -> MemoryChatGraphState:
        observations = list(state.get("tool_observations", []))
        tool_context = _tool_observations_to_context(observations)
        return {
            "tool_observation_context": tool_context,
            "prompt_context": _append_tool_context(state.get("prompt_context", ""), tool_context),
            "pending_tool_action": None,
            "tool_budget": max(int(state.get("tool_budget") or 0), 0),
            "turn_messages": [
                *state.get("turn_messages", []),
                _turn_message(
                    "assistant",
                    "我已经把工具结果纳入本轮上下文。",
                    name="observe_tool_result",
                ),
            ],
            "thought_events": [
                *state.get("thought_events", []),
                _thought(
                    "observe-tool-result",
                    "观察工具结果",
                    "工具已经返回结果，我会把它纳入下一步判断。",
                    related_node="observe_tool_result",
                ),
            ],
        }

    return observe_tool_result


def build_generate_answer_node(
    answer_generator: AnswerGenerator | None = None,
):
    """生成最终回复。

    回答生成结果会进入 checkpoint。如果模型调用后进程中断，恢复会继续执行
    persist_messages，不会重复调用大模型。
    """

    def generate_answer(state: MemoryChatGraphState) -> MemoryChatGraphState:
        user_message = _resolve_user_message(state)
        recent_messages = state.get("recent_messages", [])
        retrieved_chunks = state.get("retrieved_chunks", [])
        needs_retrieval = bool(state.get("needs_retrieval", False))
        retrieval_grade = state.get("retrieval_grade", "none")
        if answer_generator is None:
            prompt_context = state.get("prompt_context", "")
            if _requires_write_file(user_message) and not _has_successful_write_observation(state):
                prompt_context = _append_tool_context(
                    prompt_context,
                    "## 本地工具写入约束\n"
                    "用户本轮要求写入/保存本地文件，但本轮没有成功的 write_file observation。"
                    "最终回答必须明确说明尚未写入文件，不能声称已经保存或写入完成。",
                )
            return {
                "assistant_answer": generate_memory_chat_answer(
                    user_message,
                    recent_messages,
                    retrieved_chunks,
                    needs_retrieval,
                    retrieval_grade,
                    prompt_context=prompt_context,
                    turn_messages=state.get("turn_messages", []),
                )
            }
        generator = answer_generator
        return {
            "assistant_answer": generator(
                user_message,
                recent_messages,
                retrieved_chunks,
                needs_retrieval,
                retrieval_grade,
            )
        }

    return generate_answer


def route_answer_mode(state: MemoryChatGraphState) -> str:
    """根据 answer_mode 选择回答生成分支。

    普通 AiMemo 页面需要传统 token 流；桌面精灵外置聊天需要按气泡输出。
    两条分支最后都必须写入 assistant_answer，确保 persist_messages 可以复用。
    """

    if state.get("answer_mode") == "elf_bubble":
        return "generate_elf_bubble_answer"
    return "generate_answer"


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
                session.add(user)
                session.add(assistant)
                session.add(conversation)
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

            parent_id = _latest_message_id(session, conversation_id)
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

            conversation.updated_at = utc_now()
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


def _plan_agent_tool_action(state: MemoryChatGraphState) -> AgentToolActionPayload | None:
    """规划主对话循环中的下一次本地工具调用。

    这里复用 Local Operator 已有的规则快路径和轻量 LLM planner，但不再把工具结果
    作为隐藏上下文一次性塞给回答节点，而是让结果回到 agent_think 继续决策。
    """

    return _think_next_tool_action(state)


def _think_next_tool_action(state: MemoryChatGraphState) -> AgentToolActionPayload | None:
    """由主 agent loop 规划下一次工具动作。

    这个函数代表 agent_think 的结构化决策能力：它先处理跨轮写入、明确规则快路径，
    再在必要时调用 LLM planner。与旧实现不同，LLM planner 的输入包含 prompt_context、
    本轮 turn_messages 和工具 observation，避免 planner 脱离主 agent 意图单独猜参数。
    """

    user_message = _resolve_user_message(state)
    contextual_write_action = _plan_contextual_write_action(state)
    if contextual_write_action is not None:
        return contextual_write_action
    roots = _default_local_operator_workspace_roots()
    action = _rule_plan_action(user_message, workspace_roots=roots)
    if action is None and _should_try_agent_tool_planner(state):
        action = _llm_plan_agent_tool_action(state)
    return action  # type: ignore[return-value]


def _llm_plan_agent_tool_action(state: MemoryChatGraphState) -> AgentToolActionPayload | None:
    """让 LLM 基于完整主循环上下文选择本地工具。

    Local Operator 的 `_llm_plan_tool_action` 仍可复用，但它只看单段输入。这里加一层
    Memory Chat 专用提示词，把“你是 agent_think，不是孤立工具选择器”的约束写清楚。
    """

    prompt = _build_agent_tool_planner_prompt(state)
    try:
        response = get_planner_chat_model().invoke([HumanMessage(content=prompt)])
        payload = parse_json_object(str(response.content))
    except Exception:
        return _llm_plan_tool_action(_tool_planner_input(state))

    if not bool(payload.get("needs_tool")):
        return None
    confidence = float(payload.get("confidence", 0.0) or 0.0)
    if confidence < 0.55:
        return None
    tool_name = str(payload.get("tool_name") or "")
    if tool_name not in READ_TOOL_NAMES | WRITE_TOOL_NAMES:
        return None
    arguments = _normalize_tool_arguments(tool_name, dict(payload.get("arguments") or {}))
    arguments = _clean_tool_path_arguments(tool_name, arguments)
    return {
        "tool_name": tool_name,
        "arguments": arguments,
        "reason": f"agent_think 判断需要本地工具：{payload.get('reason', '')}",
    }


def _build_agent_tool_planner_prompt(state: MemoryChatGraphState) -> str:
    """构造主 agent loop 的工具决策提示词。"""

    observations = _tool_observations_to_context(list(state.get("tool_observations", [])))
    turn_messages = "\n".join(
        f"{message.get('role')}({message.get('name') or ''}): {message.get('content')}"
        for message in state.get("turn_messages", [])[-12:]
    )
    return (
        "你是 Ai 记 Memory Chat Graph 的 agent_think 节点，负责决定下一步是否调用本地文件工具。\n"
        "你不是孤立的工具选择器：必须承接本轮消息流、上一轮对话窗口、以及已经返回的工具 observation。\n\n"
        "核心原则：\n"
        "- 如果用户要求读取、查看、搜索本地文件/目录/项目，应该选择读工具。\n"
        "- 如果用户要求创建、保存、写入本地文件，且你能确定 path 和真实 content，应该选择 write_file。\n"
        "- 如果用户本轮明确要求生成某类文件，例如 HTML 网页、Python 脚本、JSON 配置，必须按该类型选择扩展名并生成真实正文；不要默认写成 Markdown。\n"
        "- 如果用户要求 HTML 网页且让你自取文件名，优先使用 index.html 或语义化 .html 文件名，content 必须是完整 HTML 文档。\n"
        "- 只有当本轮只是确认上一轮草稿，比如“你自己取文件名/按你说的写/就这样保存”时，才沿用上一轮目录和正文补文件名并 write_file。\n"
        "- 如果 get_file_info 返回某个目标文件 PATH_NOT_FOUND，而用户本意是创建文件，不要把它解释成目录不存在；下一步应考虑 write_file。\n"
        "- write_file 的 content 必须是真实正文，禁止写入“此处填写”“待补充”“TODO”等占位模板。\n"
        "- 工具已经成功写入后，不要继续调用工具，应进入最终回答。\n"
        "- 不要因为路径在 C/D/E 盘或 Home 外就拒绝规划；路径策略由工具执行层判断。\n\n"
        "可用工具：list_dir、read_file、search_files、search_text、get_file_info、write_file。\n"
        "只返回 JSON，不要输出其他文本。格式：\n"
        "{"
        "\"needs_tool\":true,"
        "\"tool_name\":\"write_file\","
        "\"arguments\":{\"path\":\"E:/test/index.html\",\"content\":\"<!doctype html>...完整 HTML...\",\"overwrite\":false},"
        "\"confidence\":0.8,"
        "\"reason\":\"简短原因\""
        "}\n\n"
        f"金字塔上下文：\n{state.get('prompt_context', '')}\n\n"
        f"本轮消息流：\n{turn_messages}\n\n"
        f"工具观察结果：\n{observations or '暂无'}\n\n"
        f"当前用户输入：{_resolve_user_message(state)}"
    )


def _should_try_agent_tool_planner(state: MemoryChatGraphState) -> bool:
    """判断是否值得让 agent_think 调用 LLM 工具规划。"""

    user_message = _resolve_user_message(state)
    if _should_try_llm_tool_planner(user_message):
        return True
    if state.get("tool_observations"):
        return True
    return _looks_like_agent_choose_filename_request(user_message)


def _tool_planner_input(state: MemoryChatGraphState) -> str:
    """构造本地工具 planner 输入。

    轻量 LLM planner 不能只看本轮 `user_message`。当用户说“按刚才那个文件保存”
    或“继续写进去”时，真正的 path/content 往往在上一轮 assistant 中。
    这里优先使用 L1+L0 当前对话窗口，让工具规划也共享同一段连续对话语义。
    """

    layer = state.get("context_conversation_window_layer") or {}
    content = str(layer.get("content") or "").strip()
    if content:
        return content
    recent_text = "\n".join(
        f"{message.get('role')}: {message.get('content')}"
        for message in state.get("recent_messages", [])[-6:]
    )
    current = f"user(current): {_resolve_user_message(state)}"
    return f"{recent_text}\n{current}".strip()


def _plan_contextual_write_action(state: MemoryChatGraphState) -> AgentToolActionPayload | None:
    """识别“按刚才说的保存/写入”这类多轮写文件请求。

    Local Operator 的规则 planner 只看当前用户输入；当上一轮 assistant 已经给出
    文件路径和正文，用户下一轮说“直接保存到文件”时，单轮 planner 会因为当前输入
    缺少 path/content 而跳过工具。这里用近期对话补齐缺失参数，保证 agent 不会
    在没有真实 write_file observation 的情况下口头声称写入成功。

    还有一类更隐蔽的多轮写入：上一轮 assistant 已经确认目录并准备了正文，但把
    文件名选择权交给用户；用户下一轮说“你自己取一个文件名吧”。这种情况下不能再
    规划 get_file_info 去检查一个尚未创建的文件，而应补一个安全文件名后直接写入。
    """

    user_message = _resolve_user_message(state)
    if _looks_like_new_write_generation_request(user_message):
        return None
    if not _looks_like_contextual_write_confirmation(user_message):
        return None
    recent_messages = state.get("recent_messages", [])
    assistant_messages = [message for message in recent_messages if message.get("role") == "assistant"]
    for message in reversed(assistant_messages[-4:]):
        content = str(message.get("content") or "")
        path = _clean_contextual_path(_extract_path(content))
        if _looks_like_agent_choose_filename_request(user_message) and (not path or _looks_like_directory_path(path)):
            path = _build_contextual_path_with_default_filename(
                content,
                directory_hint=path,
                user_message=user_message,
            )
        write_content = _extract_contextual_write_content(content, path)
        if path and write_content:
            return {
                "tool_name": "write_file",
                "arguments": {
                    "path": path,
                    "content": write_content,
                    "overwrite": _looks_like_overwrite_request(user_message),
                },
                "reason": "用户确认把上一轮 assistant 准备好的正文保存到具体文件。",
            }
    return None


def _clean_contextual_path(path: str) -> str:
    """清理从自然语言括号/句子中抽出的路径尾部标点。"""

    return _clean_tool_path(path)


def _clean_tool_path_arguments(tool_name: str, arguments: dict) -> dict:
    """清理 LLM 规划出的工具路径参数。

    LLM 经常会把 Markdown 里的反引号一起放进 JSON 路径，例如 `E:/test`。
    文件系统工具会忠实执行这个路径，于是就会创建出 `test`` 这样的目录。
    所以所有进入工具层的 path/root 都要先做一次轻量清洗。
    """

    cleaned = dict(arguments)
    for key in ["path", "root"]:
        if key in cleaned:
            cleaned[key] = _clean_tool_path(str(cleaned.get(key) or ""))
    return cleaned


def _clean_tool_path(path: str) -> str:
    """清理路径两端常见的自然语言/Markdown 包裹符。"""

    return path.strip().replace("`", "").strip(" \t\r\n").rstrip("）)。；;，,。")


def _looks_like_contextual_write_confirmation(text: str) -> bool:
    """判断当前输入是否是在确认上一轮的写入方案。"""

    write_keywords = ["保存", "写进", "写入", "放到", "存到", "直接写", "直接保存"]
    file_keywords = ["文件", ".txt", ".md", ".json", ".py", ".ts", ".tsx", ".html", ".css", "具体的"]
    if any(keyword in text for keyword in write_keywords) and any(keyword in text for keyword in file_keywords):
        return True
    return _looks_like_agent_choose_filename_request(text)


def _looks_like_agent_choose_filename_request(text: str) -> bool:
    """判断用户是否把“文件名/保存方案”的选择权交给 agent。

    这类输入本身通常没有路径和正文，例如“你自己取一个文件名吧”。它只有在近期
    assistant 消息里已经出现目录和草稿正文时才会触发 write_file。
    """

    choose_keywords = ["你自己", "你来", "你决定", "你取", "帮我取", "随便", "都行", "按你说的", "就这样"]
    filename_keywords = ["文件名", "名字", "命名", "取名", "保存", "写进去", "写入"]
    return any(keyword in text for keyword in choose_keywords) and any(
        keyword in text for keyword in filename_keywords
    )


def _build_contextual_path_with_default_filename(
    assistant_content: str,
    *,
    directory_hint: str = "",
    user_message: str = "",
) -> str:
    """从上一轮 assistant 中抽目录，并补一个默认 Markdown 文件名。

    目录识别只服务于“用户让 agent 自拟文件名”的桥接场景；如果上一轮没有明确目录，
    返回空字符串，避免凭空写到未知位置。
    """

    directory = directory_hint if _looks_like_directory_path(directory_hint) else _extract_contextual_directory_path(assistant_content)
    if not directory:
        return ""
    separator = "\\" if "\\" in directory else "/"
    return f"{directory.rstrip('/\\')}{separator}{_default_contextual_filename(assistant_content, user_message)}"


def _extract_contextual_directory_path(text: str) -> str:
    """从 assistant 自然语言中提取目录路径。

    优先识别带尾部分隔符的路径；其次识别“X 目录”前面的绝对路径。这样可以覆盖
    `E:/test/`、`E:\\test\\` 以及“E 盘下面的 test 目录”被 assistant 规范化后的说法。
    """

    quoted = re.findall(r"[\"“']([^\"”']+)[\"”']", text)
    candidates = [*quoted, *re.findall(r"([A-Za-z]:[\\/][^\s，。；;）)]*[\\/])", text)]
    candidates.extend(re.findall(r"([A-Za-z]:[\\/][^\s，。；;）)]*?)\s*(?:目录|文件夹)", text))
    for candidate in candidates:
        path = _clean_contextual_path(candidate)
        if _looks_like_directory_path(path):
            return path
    return ""


def _looks_like_directory_path(path: str) -> bool:
    """保守判断一个路径字符串是否更像目录而不是文件。"""

    if not path:
        return False
    cleaned = path.strip().rstrip("）)")
    if cleaned.endswith(("/", "\\")):
        return True
    name = re.split(r"[\\/]", cleaned)[-1]
    return "." not in name


def _default_contextual_filename(assistant_content: str, user_message: str = "") -> str:
    """为上下文写入生成稳定、可读的默认文件名。

    第一版先不用模型另起名，避免 planner 变慢；按内容语义给出少量稳定命名即可。
    """

    combined = f"{user_message}\n{assistant_content}"
    if any(keyword in combined.lower() for keyword in ["html", "网页", "页面", "website", "web page"]):
        return "index.html"
    if any(keyword in combined for keyword in ["评价", "印象", "怎么样的人"]):
        return "message_to_jiaxuan.md"
    if any(keyword in combined for keyword in ["心里话", "想对你说", "想对我说"]):
        return "memo_elf_letter.md"
    return "memo_elf_message.md"


def _looks_like_new_write_generation_request(text: str) -> bool:
    """判断当前输入是否是一个新的生成+写入请求，而不是确认上一轮草稿。

    例如“写一个好看的 html 网页，随便取文件名”虽然包含“随便取文件名”，
    但它本身已经给出了新的产物类型和内容目标，不能套用上一轮草稿的默认 .md 文件名。
    """

    generate_keywords = ["写一个", "创建一个", "新建一个", "生成一个", "做一个", "帮我写", "帮我创建"]
    artifact_keywords = [
        "html",
        "网页",
        "页面",
        "css",
        "javascript",
        "js",
        "python",
        "脚本",
        "json",
        "配置",
        "组件",
        "代码",
    ]
    return any(keyword in text for keyword in generate_keywords) and any(
        keyword.lower() in text.lower() for keyword in artifact_keywords
    )


def _extract_contextual_write_content(assistant_content: str, path: str) -> str:
    """从上一轮 assistant 消息中提取真正要写入文件的正文。

    该函数是保守启发式：去掉“是否保存”“路径建议”等操作性句子，保留 assistant
    已经生成好的正文段落。它只服务于当前 MVP，后续更理想的方案是让 agent_think
    输出结构化草稿和目标文件，避免从自然语言里反解析。
    """

    lines = assistant_content.splitlines()
    kept: list[str] = []
    normalized_path = path.replace("/", "\\")
    skip_markers = [
        "保存",
        "写入",
        "文件",
        "路径",
        "是否",
        "希望我",
        "你希望",
        "比如",
        "例如",
        normalized_path,
        path,
    ]
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if kept and kept[-1] != "":
                kept.append("")
            continue
        if any(marker and marker in line for marker in skip_markers):
            continue
        kept.append(line)
    content = "\n".join(kept).strip()
    return content if len(content) >= 8 else ""


def _has_successful_write_observation(state: MemoryChatGraphState) -> bool:
    """本轮是否真的完成过 write_file。

    最终回答必须以 observation 为事实来源。只要没有成功 observation，回答层就不能
    声称“我已经写入/保存完成”，否则用户会看到文字完成但磁盘没有文件。
    """

    return any(
        observation.get("tool_name") == "write_file" and observation.get("ok")
        for observation in state.get("tool_observations", [])
    )


def _requires_write_file(user_message: str) -> bool:
    """用户当前是否明确要求写入/保存本地文件。"""

    write_keywords = ["写入", "写进", "保存", "存到", "创建文件", "新建文件", "放到", "直接写", "直接保存"]
    file_keywords = ["文件", ".txt", ".md", ".json", ".py", ".ts", ".tsx", ".html", ".css", "目录"]
    return any(keyword in user_message for keyword in write_keywords) and any(
        keyword in user_message for keyword in file_keywords
    )


def _should_try_llm_tool_planner(user_message: str) -> bool:
    """判断是否值得调用本地工具 planner。

    普通聊天不应该每轮多一次 planner LLM；只有本地、文件、项目、仓库、路径等语义
    出现时，才让轻量模型判断是否需要 read/write 工具。
    """

    keywords = [
        "当前电脑",
        "本机",
        "本地",
        "电脑",
        "硬盘",
        "文件",
        "目录",
        "文件夹",
        "路径",
        "项目",
        "仓库",
        "repo",
        "repository",
        "代码",
        "源码",
        "写入",
        "创建",
        "保存到",
        "Ai记",
        "Ai 记",
        "AiMemo",
    ]
    return any(keyword in user_message for keyword in keywords)


def _to_agent_tool_action(action: dict, *, index: int) -> AgentToolActionPayload:
    """把 Local Operator action 规整为主对话 graph 的工具 action。"""

    tool_name = str(action.get("tool_name") or "")
    operation_type = "write" if tool_name in WRITE_TOOL_NAMES else "read"
    return {
        "tool_call_id": f"tool-{index + 1}-{tool_name or 'unknown'}",
        "tool_name": tool_name,
        "arguments": dict(action.get("arguments") or {}),
        "reason": str(action.get("reason") or ""),
        "operation_type": operation_type,
        "risk_level": "medium" if operation_type == "write" else "low",
        "requires_approval": False,
    }


def _run_agent_tool_action(
    state: MemoryChatGraphState,
    *,
    session_factory: SessionFactory,
    allowed_tool_names: set[str],
) -> MemoryChatGraphState:
    """执行主对话循环中的当前工具 action。

    工具仍通过 LangChain @tool.invoke() 调用，审计、路径策略、敏感文件拦截都复用
    `app.local_operator` 层。这样主 graph 只负责编排，不直接碰文件系统。
    """

    action = state.get("pending_tool_action") or {}
    tool_name = str(action.get("tool_name") or "")
    arguments = dict(action.get("arguments") or {})
    tool_call_id = str(action.get("tool_call_id") or "")

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
            raw_result = tools[tool_name].invoke(arguments)
            payload = parse_json_object(str(raw_result))
            observation = {
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "arguments": arguments,
                "ok": bool(payload.get("ok")),
                "data": dict(payload.get("data") or {}),
                "error_code": str(payload.get("error_code") or ""),
                "message": str(payload.get("message") or ""),
                "blocked": bool(payload.get("blocked", False)),
            }

    return {
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
            ),
        ],
    }


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
) -> AgentThoughtPayload:
    """创建一个可展示的过程摘要。"""

    return {
        "id": thought_id,
        "title": title,
        "summary": summary,
        "status": status,
        "related_node": related_node,
        "related_tool_call_id": related_tool_call_id,
    }


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
    if tool_name == "write_file":
        return f"写入完成：{data.get('relative_path') or data.get('path')}"
    if tool_name == "read_file":
        return f"读取完成：{data.get('relative_path') or data.get('path')}"
    if tool_name == "search_files":
        return f"文件搜索完成，找到 {len(data.get('matches') or [])} 个候选。"
    if tool_name == "search_text":
        return f"文本搜索完成，找到 {len(data.get('matches') or [])} 条匹配。"
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
