from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
import json
import logging
from copy import deepcopy
from pathlib import Path
import re
from typing import Literal

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage
from langgraph.types import Send
from sqlmodel import Session, desc, select

from app.ai.json_utils import parse_json_object
from app.agent.graphs.local_operator.nodes import (
    EXEC_TOOL_NAMES,
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
    GoalVerificationPayload,
    MemoryChatGraphState,
    RetrievedChunkPayload,
    TaskPayload,
    TaskStepPayload,
    TurnMessagePayload,
    WorldStatePayload,
    WorldStatusPayload,
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
            active_task = _restore_active_task_for_turn(
                conversation.active_task,
                user_message=_resolve_user_message(state),
                recent_messages=recent_messages,
            )
            active_world_state = active_task.get("world_state") if active_task else _empty_world_state()
            active_boundary: TaskBoundaryPayload = (
                {
                    "type": "continuation",
                    "reason": "当前输入是在确认继续上一轮未完成本地任务，已恢复 conversation.active_task。",
                    "previous_task_id": str(active_task.get("id") or "") or None,
                    "active_task_id": str(active_task.get("id") or "") or None,
                    "expired_task_id": None,
                }
                if active_task
                else {
                    "type": "fresh",
                    "reason": "从 START 开启的新一轮用户输入。",
                    "previous_task_id": None,
                    "active_task_id": None,
                    "expired_task_id": None,
                }
            )
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
                # 工具任务可能包含 read-before-write、失败修复和运行验证。
                # 预算只作为防失控保护，不应过早截断正常的多步任务。
                "tool_budget": 20,
                "agent_decision": {},
                "planned_tool_actions": [],
                "pending_tool_action": None,
                "task_boundary": active_boundary,
                "expired_task": {},
                "task": active_task,
                "world_state": active_world_state,
                "world_status": _empty_world_status(),
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

    L1 历史消息和 L0 当前输入必须分开注入。之前把 L1+L0 合成“连续对话窗口”，
    容易让工具 planner 把历史 assistant 草稿误当成本轮指令，导致跨任务串工具。
    """

    def merge_prompt_context(state: MemoryChatGraphState) -> MemoryChatGraphState:
        payloads: list[ContextLayerPayload] = [
            _resolve_context_layer(state, "context_l4_layer"),
            _resolve_context_layer(state, "context_l3_layer"),
            _resolve_context_layer(state, "context_l2_layer"),
            _resolve_context_layer(state, "context_l1_layer"),
            _resolve_context_layer(state, "context_l0_layer"),
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
        if loop_count > 12:
            logger.warning(
                "memory_chat agent loop guard triggered: conversation_id=%s task_id=%s observations=%s",
                state.get("conversation_id"),
                (state.get("task") or {}).get("id"),
                len(state.get("tool_observations", [])),
            )
            return {
                "agent_loop_count": loop_count,
                "agent_decision": {"type": "final_answer", "reason": "agent 工具循环超过安全上限。"},
                "turn_messages": [
                    *state.get("turn_messages", []),
                    _turn_message(
                        "assistant",
                        "工具循环超过安全上限，已停止继续调用工具。",
                        name="agent_think",
                    ),
                ],
                "thought_events": [
                    *_complete_running_thoughts(state),
                    _thought(
                        "agent-think-loop-guard",
                        "停止工具循环",
                        "本轮工具循环超过安全上限，我会基于已有结果回答，避免重复执行。",
                        related_node="agent_think",
                    ),
                ],
            }

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

        task = _task_with_step_queues(state.get("task") or {})
        ready_step = _select_ready_task_step(task)
        reasoning_thoughts: list[AgentThoughtPayload] = []
        if ready_step is not None:
            while ready_step is not None and ready_step.get("kind") == "reasoning":
                generated = _generate_reasoning_step_output(ready_step, {**state, "task": task, "world_state": task.get("world_state") or _empty_world_state()})
                task = _mark_task_step_status(task, str(ready_step.get("id")), "COMPLETED")
                world_state = _update_world_state_for_reasoning(
                    task.get("world_state") or _empty_world_state(),
                    ready_step,
                    generated,
                )
                task["world_state"] = world_state
                reasoning_thoughts.append(
                    _thought(
                        f"reasoning-step-{ready_step.get('id')}",
                        "生成中间产物",
                        str(ready_step.get("description") or "已生成后续步骤需要的中间产物。"),
                        related_node="agent_think",
                    )
                )
                state = {**state, "task": task, "world_state": world_state}
                ready_step = _select_ready_task_step(task)

            if ready_step is None:
                return {
                    "agent_loop_count": loop_count,
                    "task": task,
                    "world_state": task.get("world_state") or _empty_world_state(),
                    "agent_decision": {"type": "verify_goal", "reason": "动态任务没有剩余可执行步骤，进入目标验收。"},
                    "thought_events": [*_complete_running_thoughts(state), *reasoning_thoughts],
                }

            action = _task_step_to_tool_action(ready_step, state)
            if action is not None:
                planned_actions = [_to_agent_tool_action(action, index=0, task_boundary=_infer_task_boundary(state, action))]
                return {
                    "agent_loop_count": loop_count,
                    "task": _mark_task_step_status(task, str(ready_step.get("id")), "EXECUTING"),
                    "world_state": task.get("world_state") or _empty_world_state(),
                    "agent_decision": {
                        "type": "tool_call",
                        "reason": str(ready_step.get("description") or ""),
                        "tool_name": action.get("tool_name", ""),
                    },
                    "planned_tool_actions": planned_actions,
                    "turn_messages": [
                        *state.get("turn_messages", []),
                        _turn_message(
                            "assistant",
                            f"执行任务步骤 `{ready_step.get('id')}`：{ready_step.get('description', '')}",
                            name="agent_think",
                        ),
                    ],
                    "thought_events": [
                        *_complete_running_thoughts(state),
                        *reasoning_thoughts,
                        _thought(
                            f"task-step-{ready_step.get('id')}",
                            "执行任务步骤",
                            str(ready_step.get("description") or "执行动态任务计划中的下一步。"),
                            related_node="agent_think",
                        ),
                    ],
                }

        if task:
            world_status = _evaluate_world_status(
                task,
                task.get("world_state") or state.get("world_state") or _empty_world_state(),
                state,
            )
            if world_status.get("requires_replan"):
                replan_result = _agent_replan_response(state, task, world_status, loop_count)
                if replan_result:
                    return replan_result
                return _task_blocked_response(state, task, world_status, loop_count)
            return _task_blocked_response(state, task, world_status, loop_count)

        if task and _task_has_terminal_status(task):
            world_status = state.get("world_status") or _evaluate_world_status(
                task,
                task.get("world_state") or state.get("world_state") or _empty_world_state(),
                state,
            )
            if task.get("status") == "COMPLETED" and not world_status.get("goal_satisfied", True):
                replan_result = _agent_replan_response(state, task, world_status, loop_count)
                if replan_result:
                    return replan_result
            return {
                "agent_loop_count": loop_count,
                "agent_decision": {"type": "final_answer", "reason": "动态任务已进入终态。"},
                "thought_events": [
                    *_complete_running_thoughts(state),
                    _thought(
                        "agent-think-task-final",
                        "整理任务结果",
                        "动态任务已经没有可执行步骤，我会基于任务状态和工具结果回答。",
                        related_node="agent_think",
                    ),
                ],
            }

        if task and task.get("status") == "REPLANNING":
            world_status = state.get("world_status") or _evaluate_world_status(
                task,
                task.get("world_state") or state.get("world_state") or _empty_world_state(),
                state,
            )
            replan_result = _agent_replan_response(state, task, world_status, loop_count)
            if replan_result:
                return replan_result
            return {
                "agent_loop_count": loop_count,
                "agent_decision": {"type": "final_answer", "reason": "任务需要重规划，但当前没有可执行恢复步骤。"},
                "thought_events": [
                    *_complete_running_thoughts(state),
                    _thought(
                        "agent-think-replan-stop",
                        "停止重复执行",
                        "任务处于重规划状态，但没有生成可执行恢复步骤；我会停止继续试错。",
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

        planned_actions = [
            _to_agent_tool_action(
                item,
                index=index,
                task_boundary=_infer_task_boundary(state, action),
            )
            for index, item in enumerate(_expand_planned_actions(action))
        ]
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


def build_plan_task_node():
    """为当前用户目标生成或维护 Dynamic Execution Task。

    这个节点位于 agent_think 上游，负责“全局计划”：
      - 如果本轮已经有 task，保留它，避免工具循环中重复规划。
      - 如果没有 task，但当前输入需要本地工具，生成 Task/Step 计划。
      - 同步初始化 world_state/world_status，供 agent_think 只做执行决策。

    agent_think 后续只消费 task 与 world_status 选择下一步，不再把“全局规划”和
    “执行下一步”混在同一个职责里。
    """

    def plan_task(state: MemoryChatGraphState) -> MemoryChatGraphState:
        existing = state.get("task") or {}
        if existing:
            existing = _task_with_step_queues(existing)
            world_state = existing.get("world_state") or state.get("world_state") or _empty_world_state()
            return {
                "task": existing,
                "world_state": world_state,
                "world_status": _evaluate_world_status(existing, world_state, state),
            }

        task = _plan_dynamic_task(state)
        if not task:
            return {
                "world_state": state.get("world_state") or _empty_world_state(),
                "world_status": _empty_world_status(),
                "thought_events": [
                    *state.get("thought_events", []),
                    _thought(
                        "plan-task-skip",
                        "无需任务计划",
                        "当前输入不需要本地工具任务，后续会直接进入回答决策。",
                        related_node="plan_task",
                    ),
                ],
            }

        world_state = task.get("world_state") or _empty_world_state()
        return {
            "task": task,
            "world_state": world_state,
            "world_status": _evaluate_world_status(task, world_state, state),
            "thought_events": [
                *state.get("thought_events", []),
                _thought(
                    "plan-task-created",
                    "规划任务步骤",
                    f"已生成动态任务计划，共 {len(task.get('steps') or [])} 个步骤。",
                    related_node="plan_task",
                ),
            ],
        }

    return plan_task


def route_after_agent_think(state: MemoryChatGraphState) -> str:
    """agent_think 后的条件边。"""

    decision = state.get("agent_decision") or {}
    if decision.get("type") == "tool_call":
        return "select_tool"
    if decision.get("type") == "verify_goal":
        return "verify_goal"
    return route_answer_mode(state)


def build_verify_goal_node():
    """验收当前 Dynamic Execution Task 是否真的完成用户目标。

    该节点把“step/tool 成功”和“goal 完成”分开：工具只提供事实，验收层判断这些
    事实是否满足用户目标。第一版使用确定性规则，优先解决运行结果编造问题。
    """

    def verify_goal(state: MemoryChatGraphState) -> MemoryChatGraphState:
        task = _task_with_step_queues(state.get("task") or {})
        world_state = task.get("world_state") or state.get("world_state") or _empty_world_state()
        verification = _verify_task_goal(task, world_state, state)
        world_status = _world_status_from_verification(
            _evaluate_world_status(task, world_state, state),
            verification,
        )
        task = {**task, "world_state": world_state, "status": "COMPLETED" if verification.get("satisfied") else "REPLANNING"}  # type: ignore[typeddict-item]
        return {
            "task": task,
            "world_state": world_state,
            "world_status": world_status,
            "goal_verification": verification,
            "agent_decision": {
                "type": "final_answer" if verification.get("satisfied") else "replan",
                "reason": verification.get("reason", ""),
            },
            "thought_events": [
                *state.get("thought_events", []),
                _thought(
                    "verify-goal",
                    "验收任务目标",
                    str(verification.get("reason") or "已完成目标验收。"),
                    related_node="verify_goal",
                ),
            ],
        }

    return verify_goal


def route_after_verify_goal(state: MemoryChatGraphState) -> str:
    """目标验收后的条件边。"""

    verification = state.get("goal_verification") or {}
    return route_answer_mode(state) if verification.get("satisfied") else "agent_think"


def _agent_replan_response(
    state: MemoryChatGraphState,
    task: TaskPayload,
    world_status: WorldStatusPayload,
    loop_count: int,
) -> MemoryChatGraphState:
    """为 agent_think 构造重规划后的下一步响应。

    失败 task 必须先走 replan，不能掉到单步工具 planner 里反复试同类 exec 命令。
    """

    replanned = _replan_task_from_world_status(state, task, world_status)
    if not replanned:
        return {}
    world_state = replanned.get("world_state") or _empty_world_state()
    replanned = _task_with_step_queues(replanned)
    ready_step = _select_ready_task_step(replanned)
    action = _task_step_to_tool_action(ready_step, {**state, "task": replanned, "world_state": world_state}) if ready_step else None
    planned_actions = (
        [_to_agent_tool_action(action, index=0, task_boundary="same_turn_followup")]
        if action is not None
        else []
    )
    return {
        "agent_loop_count": loop_count,
        "task": _mark_task_step_status(replanned, str(ready_step.get("id")), "EXECUTING") if action is not None and ready_step else replanned,
        "world_state": world_state,
        "world_status": _evaluate_world_status(replanned, world_state, state),
        "agent_decision": {
            "type": "tool_call",
            "reason": str(ready_step.get("description") if ready_step else "执行重规划后的下一步。"),
            "tool_name": str(action.get("tool_name") if action else ""),
        }
        if planned_actions
        else {"type": "final_answer", "reason": "任务目标尚未满足，但暂无可执行重规划步骤。"},
        "planned_tool_actions": planned_actions,
        "thought_events": [
            *_complete_running_thoughts(state),
            _thought(
                "agent-think-replan",
                "重规划后续步骤",
                str(world_status.get("replan_reason") or "任务目标尚未完全满足，我会补充后续步骤。"),
                related_node="agent_think",
            ),
        ],
    }


def _task_blocked_response(
    state: MemoryChatGraphState,
    task: TaskPayload,
    world_status: WorldStatusPayload,
    loop_count: int,
) -> MemoryChatGraphState:
    """Task 存在但当前没有可执行 step 时的硬门禁响应。

    这里不能回退到旧单步工具 planner；否则 Task 队列和 WorldState 会脱节。
    """

    decision_reason = (
        "动态任务没有剩余可执行步骤，准备进入目标验收。"
        if world_status.get("goal_satisfied")
        else "动态任务被阻塞，且当前没有可执行重规划步骤。"
    )
    return {
        "agent_loop_count": loop_count,
        "task": _task_with_step_queues(task),
        "world_state": task.get("world_state") or _empty_world_state(),
        "world_status": world_status,
        "agent_decision": {
            "type": "verify_goal" if world_status.get("goal_satisfied") else "final_answer",
            "reason": decision_reason,
        },
        "thought_events": [
            *_complete_running_thoughts(state),
            _thought(
                "agent-think-task-blocked",
                "任务等待验收" if world_status.get("goal_satisfied") else "任务执行受阻",
                str(world_status.get("replan_reason") or decision_reason),
                related_node="agent_think",
            ),
        ],
    }


def build_select_tool_node():
    """从主对话工具队列中取出下一次工具调用。"""

    def select_tool(state: MemoryChatGraphState) -> MemoryChatGraphState:
        planned_actions = list(state.get("planned_tool_actions", []))
        if not planned_actions:
            return {"pending_tool_action": None}
        next_action = dict(planned_actions.pop(0))
        next_action["status"] = "EXECUTING"
        return {
            "pending_tool_action": next_action,
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
        if tool_name in READ_TOOL_NAMES | WRITE_TOOL_NAMES | EXEC_TOOL_NAMES:
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
        tool_name = action.get("tool_name")
        if tool_name in WRITE_TOOL_NAMES:
            return "run_write_tool"
        if tool_name in EXEC_TOOL_NAMES:
            return "run_exec_tool"
        return "run_read_tool"
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


def build_run_exec_tool_node(session_factory: SessionFactory):
    """执行主对话循环中的 exec 工具。

    exec 和 read/write 同属 agent 工具循环，但必须单独路由：它代表终端命令，
    风险、审计、前端展示都和文件读写不同。
    """

    def run_exec_tool(state: MemoryChatGraphState) -> MemoryChatGraphState:
        return _run_agent_tool_action(state, session_factory=session_factory, allowed_tool_names=EXEC_TOOL_NAMES)

    return run_exec_tool


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
        if _requires_local_operation_followup(state) and not state.get("tool_observations"):
            return {
                "assistant_answer": (
                    "我还没有实际执行这一步。\n\n"
                    "这轮输入看起来是在确认继续上一轮的本地操作，但本轮没有任何 "
                    "`write_file` 或 `exec_command` 工具结果，所以我不能声称已经覆盖文件、"
                    "也不能给出运行结果。请让我继续调用本地工具完成这一步。"
                )
            }
        if answer_generator is None:
            prompt_context = state.get("prompt_context", "")
            task = _task_with_step_queues(state.get("task") or {})
            world_status = state.get("world_status") or {}
            if task and not bool(world_status.get("goal_satisfied")):
                prompt_context = _append_tool_context(
                    prompt_context,
                    "## 动态任务未完成约束\n"
                    "当前存在未完成的 Dynamic Execution Task，且 goal_satisfied=false。"
                    "最终回答必须明确说明任务未完成和阻塞原因，不能声称已经完成用户目标，"
                    "也不能声称自己没有本地工具能力。若缺少运行结果，必须说明尚未成功执行命令。",
                )
            if _requires_write_file(user_message) and not _has_successful_write_observation(state):
                prompt_context = _append_tool_context(
                    prompt_context,
                    "## 本地工具写入约束\n"
                    "用户本轮要求写入/保存本地文件，但本轮没有成功的 write_file observation。"
                    "最终回答必须明确说明尚未写入文件，不能声称已经保存或写入完成。",
                )
            if _requires_exec_result_followup(state) and not _has_successful_exec_observation(state.get("tool_observations", [])):
                prompt_context = _append_tool_context(
                    prompt_context,
                    "## 本地命令运行约束\n"
                    "当前用户请求或多轮上下文要求运行/编译/测试并返回结果，但本轮没有成功的 "
                    "exec_command observation。最终回答必须说明尚未成功运行命令，不能编造运行结果、"
                    "随机数、测试通过或构建成功。",
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
                conversation.active_task = _serialize_active_task_for_conversation(state)
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
            conversation.active_task = _serialize_active_task_for_conversation(state)
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


def _resolve_runtime_task(state: MemoryChatGraphState) -> TaskPayload:
    """解析或创建本轮 Dynamic Execution Task。

    第一版 Task 只保存在 graph state/checkpoint 中。若当前输入没有本地工具意图，
    返回空 dict，让旧的直接回答路径继续工作。
    """

    existing = state.get("task") or {}
    # 同一轮工具循环会多次回到 agent_think。只要 state 里已有 task，就必须继续使用
    # 这个 task 的状态机，包括 COMPLETED/FAILED 等终态。否则终态 task 会被按同一条
    # L0 输入重新规划，形成 read -> write -> completed -> replan -> read 的死循环。
    if existing:
        return existing
    plan = _plan_dynamic_task(state)
    return plan or {}


def _plan_dynamic_task(state: MemoryChatGraphState) -> TaskPayload | None:
    """为当前输入生成动态执行计划。

    这里先使用 LLM planner，失败时再退回现有单 action planner。注意：退回路径只是
    兼容层，不新增具体请求特化；真正长期方向是让 planner 输出完整 Task。
    """

    should_try = _should_try_agent_tool_planner(state) or _requires_local_operation_followup(state)
    if not should_try:
        return None
    task = _llm_plan_dynamic_task(state)
    if task:
        return task
    action = _plan_contextual_write_action(state)
    if action is None:
        roots = _default_local_operator_workspace_roots()
        action = _rule_plan_action(_resolve_user_message(state), workspace_roots=roots)
    if action is None and should_try:
        action = _llm_plan_agent_tool_action(state)
    if action is None:
        return None
    return _task_from_single_action(state, action)


def _task_with_step_queues(task: TaskPayload) -> TaskPayload:
    """确保 task 同时具备 pending/completed/failed 三个执行队列。

    这是双队列模型的兼容层：新逻辑读写队列字段，旧的 `steps` 仍作为所有 step
    的镜像，供现有 graph 可视化、节点详情和测试读取。
    """

    if not task:
        return task
    updated = dict(task)
    if not any(key in updated for key in ("pending_steps", "completed_steps", "failed_steps")):
        pending: list[TaskStepPayload] = []
        completed: list[TaskStepPayload] = []
        failed: list[TaskStepPayload] = []
        for step in list(updated.get("steps") or []):
            status = step.get("status")
            if status == "COMPLETED":
                completed.append(dict(step))  # type: ignore[arg-type]
            elif status == "FAILED":
                failed.append(dict(step))  # type: ignore[arg-type]
            elif status == "EXECUTING":
                pending.append({**dict(step), "status": "PENDING"})  # type: ignore[arg-type]
            else:
                pending.append(dict(step))  # type: ignore[arg-type]
        updated["pending_steps"] = pending
        updated["completed_steps"] = completed
        updated["failed_steps"] = failed
    else:
        updated["pending_steps"] = list(updated.get("pending_steps") or [])
        updated["completed_steps"] = list(updated.get("completed_steps") or [])
        updated["failed_steps"] = list(updated.get("failed_steps") or [])
    updated["pending_steps"] = _dedupe_task_steps(list(updated.get("pending_steps") or []), keep="first")
    updated["completed_steps"] = _dedupe_task_steps(list(updated.get("completed_steps") or []), keep="last")
    updated["failed_steps"] = _dedupe_task_steps(list(updated.get("failed_steps") or []), keep="last")
    previous_status = str(updated.get("status") or "")
    updated["steps"] = _task_steps_snapshot(updated)  # type: ignore[arg-type]
    updated["status"] = (
        "REPLANNING" if previous_status == "REPLANNING" else _derive_task_status_from_queues(updated)
    )  # type: ignore[typeddict-item]
    return updated  # type: ignore[return-value]


def _dedupe_task_steps(steps: list[TaskStepPayload], *, keep: Literal["first", "last"]) -> list[TaskStepPayload]:
    """按 step.id 去重，并累计 attempt_count。

    pending 队列保留第一次出现的位置，避免 replanner 把相同 step 重复插入队首；
    completed/failed 历史保留最后一次结果，避免历史列表无限膨胀。
    """

    result: list[TaskStepPayload] = []
    index_by_id: dict[str, int] = {}
    for raw_step in steps:
        step = dict(raw_step)
        step_id = str(step.get("id") or "")
        if not step_id:
            result.append(step)  # type: ignore[arg-type]
            continue
        attempt_count = int(step.get("attempt_count") or step.get("retry_count") or 0)
        if step_id not in index_by_id:
            step["attempt_count"] = max(1, attempt_count or 1)
            index_by_id[step_id] = len(result)
            result.append(step)  # type: ignore[arg-type]
            continue
        existing = dict(result[index_by_id[step_id]])
        merged_attempt = max(
            int(existing.get("attempt_count") or existing.get("retry_count") or 1),
            attempt_count or 1,
        ) + 1
        if keep == "last":
            step["attempt_count"] = merged_attempt
            if not step.get("last_error"):
                step["last_error"] = existing.get("last_error")
            result[index_by_id[step_id]] = step  # type: ignore[assignment]
        else:
            existing["attempt_count"] = merged_attempt
            if step.get("last_error"):
                existing["last_error"] = step.get("last_error")
            result[index_by_id[step_id]] = existing  # type: ignore[assignment]
    return result


def _task_steps_snapshot(task: TaskPayload) -> list[TaskStepPayload]:
    """生成兼容旧 UI 的 steps 镜像。"""

    snapshot: list[TaskStepPayload] = []
    snapshot.extend(dict(step) for step in task.get("completed_steps") or [])  # type: ignore[arg-type]
    current_id = str(task.get("current_step_id") or "")
    for step in task.get("pending_steps") or []:
        updated = dict(step)
        if current_id and str(updated.get("id")) == current_id:
            updated["status"] = "EXECUTING"
        snapshot.append(updated)  # type: ignore[arg-type]
    snapshot.extend(dict(step) for step in task.get("failed_steps") or [])  # type: ignore[arg-type]
    return snapshot


def _derive_task_status_from_queues(task: TaskPayload) -> str:
    """根据执行队列推导 task 状态。"""

    if task.get("current_step_id"):
        return "RUNNING"
    if task.get("pending_steps"):
        return "READY"
    return "COMPLETED"


def _new_task_payload(
    *,
    base: TaskPayload | None,
    state: MemoryChatGraphState,
    goal: str,
    source_user_message: str,
    steps: list[TaskStepPayload],
    world_state: WorldStatePayload,
    execution_history: list[dict],
    plan_version: int,
    replan_count: int,
    pending_merge: Literal["replace", "prepend", "append"] = "replace",
) -> TaskPayload:
    """创建使用 pending/completed/failed 队列的 TaskPayload。"""

    base = _task_with_step_queues(base or {})
    task_id = str(base.get("id") or _task_id_from_user_message(_resolve_user_message(state)))
    new_steps = _dedupe_task_steps([dict(step) for step in steps], keep="first")  # type: ignore[list-item]
    pending_steps = new_steps
    if pending_merge == "prepend":
        # 当前失败 step 的恢复步骤插入队首，尚未执行的原计划留在队尾继续等待。
        pending_steps.extend(dict(step) for step in base.get("pending_steps") or [])  # type: ignore[arg-type]
    elif pending_merge == "append":
        pending_steps = [dict(step) for step in base.get("pending_steps") or []]  # type: ignore[list-item]
        pending_steps.extend(new_steps)
    task: TaskPayload = {
        "id": task_id,
        "goal": goal,
        "source_user_message": source_user_message,
        "status": "READY",
        "plan_version": plan_version,
        "current_step_id": None,
        "pending_steps": pending_steps,
        "completed_steps": list(base.get("completed_steps") or []),
        "failed_steps": list(base.get("failed_steps") or []),
        "steps": [],
        "world_state": world_state,
        "execution_history": execution_history,
        "replan_count": replan_count,
    }
    return _task_with_step_queues(task)


def _llm_plan_dynamic_task(state: MemoryChatGraphState) -> TaskPayload | None:
    """让 planner 直接输出 Task/Step 计划。"""

    prompt = _build_dynamic_task_planner_prompt(state)
    try:
        response = get_planner_chat_model().invoke([HumanMessage(content=prompt)])
        payload = parse_json_object(str(response.content))
    except Exception:
        return None
    if not bool(payload.get("needs_task", True)):
        return None
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        return None
    steps = [_normalize_task_step(raw_step, index=index) for index, raw_step in enumerate(raw_steps) if isinstance(raw_step, dict)]
    steps = [step for step in steps if step.get("kind") in {"tool", "reasoning", "decision", "final"}]
    if not steps:
        return None
    return _new_task_payload(
        base=None,
        state=state,
        goal=str(payload.get("goal") or _resolve_user_message(state)),
        source_user_message=_resolve_user_message(state),
        steps=steps,
        world_state=_empty_world_state(),
        execution_history=[
            {
                "type": "planned",
                "summary": str(payload.get("reason") or "planner 生成动态任务计划。"),
                "payload": {"step_count": len(steps)},
            }
        ],
        plan_version=1,
        replan_count=0,
    )


def _replan_task_from_world_status(
    state: MemoryChatGraphState,
    task: TaskPayload,
    world_status: WorldStatusPayload,
) -> TaskPayload | None:
    """基于 WorldStatus 重规划剩余步骤。

    第一版优先调用同一个 Dynamic Task Planner，但会把当前 task/world_state/world_status
    放进输入，要求 planner 只补后续步骤。LLM 失败时使用一个通用兜底：如果目标缺少
    “运行结果”，且 world_state 已有写入文件，则补一个 exec_command step。
    """

    deterministic = _deterministic_replan_from_world_status(state, task, world_status)
    if deterministic:
        return deterministic
    # 如果已经有未解决的工具失败，优先让通用 replanner 读取 stderr/stdout 和
    # WorldState 后决定怎么修；只有“没有失败、只是缺运行结果”时才走机械 fallback。
    has_active_failure = bool(world_status.get("last_error"))
    if not has_active_failure:
        fallback = _fallback_replan_missing_exec_result(state, task, world_status)
        if fallback:
            return fallback
    # replan_count 只限制语义 LLM 重规划，确定性恢复不应被旧预算挡住。
    if int(task.get("replan_count") or 0) >= 5:
        return None
    replanned = _llm_replan_dynamic_task(state, task, world_status)
    if replanned:
        return replanned
    patched = _fallback_drop_unreferenced_failed_step(state, task, world_status)
    if patched:
        return patched
    return _fallback_replan_missing_exec_result(state, task, world_status)


def _deterministic_replan_from_world_status(
    state: MemoryChatGraphState,
    task: TaskPayload,
    world_status: WorldStatusPayload,
) -> TaskPayload | None:
    """处理可确定恢复的工具契约错误。"""

    if world_status.get("recovery_hint") == "read_then_write_same_path":
        return _replan_read_before_write_same_path(state, task, world_status)
    return None


def _replan_read_before_write_same_path(
    state: MemoryChatGraphState,
    task: TaskPayload,
    world_status: WorldStatusPayload,
) -> TaskPayload | None:
    """READ_BEFORE_WRITE_REQUIRED 的确定性恢复。

    工具已经明确告诉我们：同一路径覆盖前必须先读。这里不交给 LLM 猜，直接补：
      read_file(original_path) -> write_file(original_path, original_content, overwrite=true)
    这样可以防止 agent 改写其他路径来绕过安全规则。
    """

    failed_step = _find_task_step(task, str(world_status.get("blocked_step_id") or ""))
    if not failed_step or failed_step.get("tool_name") != "write_file":
        return None
    arguments = dict(failed_step.get("arguments") or {})
    path = str(arguments.get("path") or world_status.get("recovery_path") or "")
    if not path:
        return None
    read_step_id = f"read_before_{failed_step.get('id') or 'write'}"
    retry_step_id = f"retry_{failed_step.get('id') or 'write'}"
    world_state = task.get("world_state") or state.get("world_state") or _empty_world_state()
    retry_arguments = dict(arguments)
    retry_arguments["path"] = path
    retry_arguments["overwrite"] = True
    steps = [
            {
                "id": read_step_id,
                "description": f"覆盖写入前读取原文件：{path}",
                "kind": "tool",
                "tool_name": "read_file",
                "arguments": {"path": path},
                "dependencies": [],
                "status": "PENDING",
                "retry_count": 0,
                "output_ref": None,
                "error": None,
            },
            {
                "id": retry_step_id,
                "description": f"读取后重试覆盖写入：{path}",
                "kind": "tool",
                "tool_name": "write_file",
                "arguments": retry_arguments,
                "dependencies": [read_step_id],
                "status": "PENDING",
                "retry_count": int(failed_step.get("retry_count") or 0) + 1,
                "output_ref": None,
                "error": None,
            },
        ]
    return _new_task_payload(
        base=_task_with_step_queues(task),
        state=state,
        goal=str(task.get("goal") or _resolve_user_message(state)),
        source_user_message=str(task.get("source_user_message") or _resolve_user_message(state)),
        steps=steps,  # type: ignore[arg-type]
        world_state=world_state,
        execution_history=[
            *list(task.get("execution_history") or []),
            {
                "type": "replanned",
                "summary": "write_file 触发 read-before-write 保护，补充同路径读取后重试写入。",
                "payload": {
                    "recovery_hint": "read_then_write_same_path",
                    "path": path,
                    "failed_step_id": failed_step.get("id"),
                },
            },
        ],
        plan_version=int(task.get("plan_version") or 1) + 1,
        # read-before-write 是机械恢复，不消耗语义重规划预算。
        replan_count=int(task.get("replan_count") or 0),
        pending_merge="prepend",
    )


def _find_task_step(task: TaskPayload, step_id: str) -> TaskStepPayload | None:
    """按 step id 查找任务步骤。"""

    if not step_id:
        return None
    queued = _task_with_step_queues(task)
    for step in (
        list(queued.get("pending_steps") or [])
        + list(queued.get("completed_steps") or [])
        + list(queued.get("failed_steps") or [])
        + list(queued.get("steps") or [])
    ):
        if str(step.get("id")) == step_id:
            return step
    return None


def _llm_replan_dynamic_task(
    state: MemoryChatGraphState,
    task: TaskPayload,
    world_status: WorldStatusPayload,
) -> TaskPayload | None:
    """让 planner 基于当前事实补充剩余步骤。"""

    world_state = task.get("world_state") or state.get("world_state") or _empty_world_state()
    recent_failures = _recent_failures_for_prompt(world_state)
    prompt = (
        "你是 AiMemo 的 Dynamic Execution Replanner。当前任务还没有满足用户目标，"
        "请基于已有 task、world_state、world_status 只规划剩余步骤，不要重复已经成功完成的步骤。\n\n"
        "规则：\n"
        "- 如果已有文件写入成功，而目标要求运行结果，应补 exec_command。\n"
        "- 如果工具失败，应基于 error/stderr 修改当前或后续步骤。\n"
        "- 你必须先分析最近失败 observation 的 error_code/stdout/stderr，再决定新步骤。\n"
        "- 禁止无修改地重复执行完全相同的失败工具调用；如果要重试同类工具，必须先改变前置条件，例如读取信息、修改文件、调整参数或生成新的中间产物。\n"
        "- replan 只能生成能推进 WorldState 的步骤；不要生成已经成功完成且无需变更的重复步骤。\n"
        "- 如果你确实需要重试旧 step，必须使用新的 step id，或在描述中明确 retry 的变化点。\n"
        "- 如果失败 step 不再被任何 pending step 依赖，且继续执行剩余 pending 可以推进任务，可以返回 plan_patch.drop_failed_step。\n"
        "- 如果 write_file 失败且 error_code=READ_BEFORE_WRITE_REQUIRED，必须补 read_file 原 write_file.path，再重试 write_file 原路径；不能换到其他路径。\n"
        "- exec_command 必须设置合理 cwd；如果要运行某个文件，cwd 应靠近该文件所在目录。\n"
        "- 只返回 JSON，格式可以是 Task Planner steps，或 {\"plan_patch\":{\"action\":\"drop_failed_step\",\"step_id\":\"...\",\"reason\":\"...\"}}。\n\n"
        f"current_user_message:\n{_resolve_user_message(state)}\n\n"
        f"recent_failures:\n{json.dumps(recent_failures, ensure_ascii=False)}\n\n"
        f"task:\n{json.dumps(task, ensure_ascii=False)}\n\n"
        f"world_status:\n{json.dumps(world_status, ensure_ascii=False)}"
    )
    debug_base = {
        "kind": "llm_replan",
        "task_id": task.get("id"),
        "plan_version": task.get("plan_version"),
        "replan_count": task.get("replan_count"),
        "world_status": _replan_debug_compact(world_status),
        "recent_failures": _replan_debug_compact(recent_failures),
        "prompt_excerpt": _truncate_debug_text(prompt, 3000),
    }
    try:
        response = get_planner_chat_model().invoke([HumanMessage(content=prompt)])
        raw_response = str(response.content)
    except Exception as exc:
        _append_replan_debug_to_task(
            task,
            {
                **debug_base,
                "status": "model_error",
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        return None
    try:
        payload = parse_json_object(raw_response)
    except Exception as exc:
        _append_replan_debug_to_task(
            task,
            {
                **debug_base,
                "status": "parse_error",
                "raw_response": _truncate_debug_text(raw_response, 4000),
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        return None
    patched = _apply_plan_patch_payload(state, task, world_status, payload)
    if patched:
        _append_replan_debug_to_task(
            patched,
            {
                **debug_base,
                "status": "accepted_patch",
                "raw_response": _truncate_debug_text(raw_response, 4000),
                "parsed_payload": _replan_debug_compact(payload),
            },
        )
        return patched
    raw_steps = payload.get("steps")
    if not isinstance(raw_steps, list) or not raw_steps:
        _append_replan_debug_to_task(
            task,
            {
                **debug_base,
                "status": "rejected_no_steps",
                "raw_response": _truncate_debug_text(raw_response, 4000),
                "parsed_payload": _replan_debug_compact(payload),
            },
        )
        return None
    steps = [_normalize_task_step(raw_step, index=index) for index, raw_step in enumerate(raw_steps) if isinstance(raw_step, dict)]
    steps = [step for step in steps if step.get("kind") in {"tool", "reasoning", "decision", "final"}]
    if not steps:
        _append_replan_debug_to_task(
            task,
            {
                **debug_base,
                "status": "rejected_invalid_steps",
                "raw_response": _truncate_debug_text(raw_response, 4000),
                "parsed_payload": _replan_debug_compact(payload),
            },
        )
        return None
    replanned = _new_task_payload(
        base=_task_with_step_queues(task),
        state=state,
        goal=str(task.get("goal") or _resolve_user_message(state)),
        source_user_message=str(task.get("source_user_message") or _resolve_user_message(state)),
        steps=steps,
        world_state=world_state,
        execution_history=[
            *list(task.get("execution_history") or []),
            {
                "type": "replanned",
                "summary": str(payload.get("reason") or world_status.get("replan_reason") or "补充剩余步骤。"),
                "payload": {"step_count": len(steps), "missing_requirements": world_status.get("missing_requirements") or []},
            },
        ],
        plan_version=int(task.get("plan_version") or 1) + 1,
        replan_count=int(task.get("replan_count") or 0) + 1,
        pending_merge=_pending_merge_strategy(world_status),
    )
    _append_replan_debug_to_task(
        replanned,
        {
            **debug_base,
            "status": "accepted_steps",
            "raw_response": _truncate_debug_text(raw_response, 4000),
            "parsed_payload": _replan_debug_compact(payload),
            "normalized_step_count": len(steps),
            "normalized_step_ids": [step.get("id") for step in steps],
        },
    )
    return replanned


def _append_replan_debug_to_task(task: TaskPayload, entry: dict) -> TaskPayload:
    """把 replanner 调试记录写入 task.world_state.replan_debug。

    参数：
      task: 当前或新生成的 Task。函数会原地更新，便于失败路径也能留下记录。
      entry: 单次 replanner 调用的压缩调试记录。

    返回：
      已带有调试记录的 task，方便调用方链式返回。
    """

    world_state = dict(task.get("world_state") or _empty_world_state())
    debug_items = list(world_state.get("replan_debug") or [])
    debug_items.append(entry)
    world_state["replan_debug"] = debug_items[-10:]
    task["world_state"] = world_state  # type: ignore[typeddict-item]
    return task


def _replan_debug_compact(value, *, depth: int = 0):
    """压缩 replanner 调试值，避免 checkpoint/debug_payload 过大。"""

    if depth >= 4:
        return _truncate_debug_text(str(value), 800)
    if isinstance(value, dict):
        return {str(key): _replan_debug_compact(item, depth=depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        items = [_replan_debug_compact(item, depth=depth + 1) for item in value[:10]]
        if len(value) > 10:
            items.append({"__truncated__": len(value) - 10})
        return items
    if isinstance(value, str):
        return _truncate_debug_text(value, 1200)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return _truncate_debug_text(str(value), 1200)


def _truncate_debug_text(text: str, max_length: int) -> str:
    """裁剪调试文本并标记被裁剪的长度。"""

    content = str(text or "")
    if len(content) <= max_length:
        return content
    return f"{content[:max_length]}\n...[truncated {len(content) - max_length} chars]"


def _pending_merge_strategy(world_status: WorldStatusPayload) -> Literal["replace", "prepend", "append"]:
    """根据重规划原因决定新 steps 如何合并进 pending 队列。"""

    last_error = world_status.get("last_error") or {}
    error_code = str(last_error.get("error_code") or "")
    if error_code in {"INVALID_PLAN_DEPENDENCY", "NO_READY_STEP"}:
        return "replace"
    if last_error:
        return "prepend"
    if world_status.get("missing_requirements"):
        return "append"
    return "replace"


def _apply_plan_patch_payload(
    state: MemoryChatGraphState,
    task: TaskPayload,
    world_status: WorldStatusPayload,
    payload: dict,
) -> TaskPayload | None:
    """应用 replanner 返回的通用 plan_patch。"""

    patch = payload.get("plan_patch")
    if not isinstance(patch, dict):
        return None
    if str(patch.get("action") or "") != "drop_failed_step":
        return None
    step_id = str(patch.get("step_id") or world_status.get("blocked_step_id") or "")
    reason = str(patch.get("reason") or payload.get("reason") or "失败 step 不再阻塞剩余队列，继续执行 pending。")
    return _drop_failed_step_if_unreferenced(state, task, world_status, step_id=step_id, reason=reason)


def _fallback_drop_unreferenced_failed_step(
    state: MemoryChatGraphState,
    task: TaskPayload,
    world_status: WorldStatusPayload,
) -> TaskPayload | None:
    """LLM 没给 steps/patch 时，对不再被依赖的失败 step 做通用 drop。"""

    last_error = world_status.get("last_error") or {}
    step_id = str(last_error.get("step_id") or world_status.get("blocked_step_id") or "")
    if not step_id:
        return None
    return _drop_failed_step_if_unreferenced(
        state,
        task,
        world_status,
        step_id=step_id,
        reason="失败 step 不再被 pending 依赖，保留失败历史并继续执行剩余队列。",
    )


def _drop_failed_step_if_unreferenced(
    state: MemoryChatGraphState,
    task: TaskPayload,
    world_status: WorldStatusPayload,
    *,
    step_id: str,
    reason: str,
) -> TaskPayload | None:
    """如果失败 step 不再被 pending 依赖，则解除 REPLANNING 并继续 pending。"""

    queued = _task_with_step_queues(task)
    if not step_id or _pending_depends_on_step(queued, step_id):
        return None
    if not queued.get("pending_steps"):
        return None
    updated = dict(queued)
    updated["status"] = "READY"
    updated["current_step_id"] = None
    updated["plan_version"] = int(queued.get("plan_version") or 1) + 1
    updated["execution_history"] = [
        *list(queued.get("execution_history") or []),
        {
            "type": "plan_patched",
            "summary": reason,
            "payload": {
                "action": "drop_failed_step",
                "step_id": step_id,
                "replan_reason": world_status.get("replan_reason"),
            },
        },
    ]
    return _task_with_step_queues(updated)  # type: ignore[arg-type]


def _pending_depends_on_step(task: TaskPayload, step_id: str) -> bool:
    """检查 pending 队列是否仍依赖某个失败 step。"""

    for step in task.get("pending_steps") or []:
        if step_id in [str(item) for item in step.get("dependencies") or []]:
            return True
    return False


def _recent_failures_for_prompt(world_state: WorldStatePayload, *, limit: int = 3) -> list[dict]:
    """提取最近失败摘要，放到 replanner prompt 的显眼位置。"""

    failures = list(world_state.get("failures") or [])
    return failures[-limit:]


def _fallback_replan_missing_exec_result(
    state: MemoryChatGraphState,
    task: TaskPayload,
    world_status: WorldStatusPayload,
) -> TaskPayload | None:
    """缺少运行结果时的通用兜底重规划。

    这个 fallback 只负责“还没尝试运行”时补一个运行步骤。若相同 exec
    已经失败，且失败后没有新的读/写/检索 observation 改变前置条件，就不能
    机械重复同一命令，否则 agent 会在同一个错误上空转。
    """

    missing = " ".join(world_status.get("missing_requirements") or [])
    if "运行结果" not in missing and "执行命令" not in missing:
        return None
    world_state = task.get("world_state") or state.get("world_state") or _empty_world_state()
    written_files = dict(world_state.get("written_files") or {})
    if not written_files:
        return None
    path = sorted(written_files)[-1]
    cwd = str(Path(path).parent).replace("\\", "/") if path else "."
    command = _infer_run_command_for_file(path)
    if not command:
        return None
    if _has_unresolved_same_exec_failure(world_state, command=command, cwd=cwd):
        return None
    steps = [
            {
                "id": "run_generated_file",
                "description": "运行已生成的文件并获取结果",
                "kind": "tool",
                "tool_name": "exec_command",
                "arguments": {"command": command, "cwd": cwd, "timeout_ms": 30000, "max_output_bytes": 65536},
                "dependencies": [],
                "status": "PENDING",
                "retry_count": 0,
                "output_ref": None,
                "error": None,
            }
        ]
    return _new_task_payload(
        base=_task_with_step_queues(task),
        state=state,
        goal=str(task.get("goal") or _resolve_user_message(state)),
        source_user_message=str(task.get("source_user_message") or _resolve_user_message(state)),
        steps=steps,  # type: ignore[arg-type]
        world_state=world_state,
        execution_history=[
            *list(task.get("execution_history") or []),
            {
                "type": "replanned",
                "summary": "目标仍缺少运行结果，补充执行命令步骤。",
                "payload": {"missing_requirements": world_status.get("missing_requirements") or []},
            },
        ],
        plan_version=int(task.get("plan_version") or 1) + 1,
        replan_count=int(task.get("replan_count") or 0) + 1,
        pending_merge=_pending_merge_strategy(world_status),
    )


def _has_unresolved_same_exec_failure(
    world_state: WorldStatePayload,
    *,
    command: str,
    cwd: str,
) -> bool:
    """判断相同 exec 失败后是否缺少新的前置条件变化。

    参数：
      world_state: 当前任务的事实状态，包含工具 observations。
      command: fallback 准备再次执行的命令。
      cwd: fallback 准备再次执行的工作目录。

    返回 True 表示“同一命令刚失败过，且失败后没有任何可改变判断的动作”，
    此时应阻止 fallback 重复入队。
    """

    observations = list(world_state.get("observations") or [])
    normalized_command = _normalize_exec_command_signature(command)
    normalized_cwd = _normalize_exec_cwd_signature(cwd)
    for index in range(len(observations) - 1, -1, -1):
        observation = observations[index]
        if observation.get("tool_name") != "exec_command" or observation.get("ok"):
            continue
        data = dict(observation.get("data") or {})
        arguments = dict(observation.get("arguments") or {})
        failed_command = str(data.get("command") or arguments.get("command") or "")
        failed_cwd = str(data.get("cwd") or arguments.get("cwd") or "")
        if _normalize_exec_command_signature(failed_command) != normalized_command:
            continue
        if _normalize_exec_cwd_signature(failed_cwd) != normalized_cwd:
            continue
        later_observations = observations[index + 1 :]
        return not _has_precondition_changing_observation(later_observations)
    return False


def _has_precondition_changing_observation(observations: list[dict]) -> bool:
    """检查失败后是否出现了足以支撑重试的新事实。

    成功写入会改变文件系统；成功读取/搜索/查看会改变 agent 对世界的认知。
    这些都可以作为“重新尝试同类命令”的前置条件。单纯再次失败不算变化。
    """

    precondition_tools = {
        "read_file",
        "write_file",
        "list_dir",
        "get_file_info",
        "search_files",
        "search_text",
    }
    return any(
        observation.get("ok") and observation.get("tool_name") in precondition_tools
        for observation in observations
    )


def _normalize_exec_command_signature(command: str) -> str:
    """把命令归一化成适合比较重复执行的签名。"""

    return " ".join(str(command or "").strip().split()).lower()


def _normalize_exec_cwd_signature(cwd: str) -> str:
    """把 cwd 归一化，避免斜杠差异影响重复执行判断。"""

    return str(cwd or ".").replace("\\", "/").rstrip("/").lower() or "."


def _infer_run_command_for_file(path: str) -> str:
    """根据文件扩展名推断短时运行命令。"""

    suffix = Path(path).suffix.lower()
    name = Path(path).name
    if suffix == ".py":
        return f"python {name}"
    if suffix == ".js":
        return f"node {name}"
    if suffix == ".rs":
        exe = Path(name).with_suffix(".exe").name
        return f"rustc {name} -o {exe} && .\\{exe}"
    return ""


def _last_written_file_path(world_state: WorldStatePayload) -> str:
    """读取最近记录的写入文件路径。"""

    written_files = dict(world_state.get("written_files") or {})
    if written_files:
        return str(list(written_files.keys())[-1])
    for observation in reversed(list(world_state.get("observations") or [])):
        if observation.get("tool_name") == "write_file" and observation.get("ok"):
            data = observation.get("data") or {}
            if isinstance(data, str):
                data = parse_json_object(data)
            path = str((data or {}).get("path") or "")
            if path:
                return path
    return ""


def _build_dynamic_task_planner_prompt(state: MemoryChatGraphState) -> str:
    """构造 Dynamic Execution Task planner 提示词。"""

    recent_text = "\n".join(
        f"{message.get('role')}: {message.get('content')}"
        for message in state.get("recent_messages", [])[-6:]
    )
    observations = _tool_observations_to_context(list(state.get("tool_observations", [])))
    followup_hint = _local_operation_followup_hint(state)
    return (
        "你是 AiMemo 的 Dynamic Execution Task Planner。你的任务是把当前用户请求拆成可执行步骤，"
        "不要只选择一个工具。\n\n"
        "必须区分 history 和 current：history 只能作为背景；current 是本轮任务边界。\n"
        "如果 current 是新的明确目标、路径或命令，不能延续 history 中上一轮工具任务。\n\n"
        "如果 current 是“可以/继续/直接覆盖/按你说的”这类确认，而 history 中最近一条 assistant "
        "明确提出了需要覆盖、保存、运行、测试或继续调用本地工具，则应把它视为上一轮本地操作的 continuation，"
        "从 history 中提取路径、内容、命令和预期结果来规划真实工具步骤。\n\n"
        "可用 step.kind：tool、reasoning、decision、final。\n"
        "可用 tool：list_dir、read_file、search_files、search_text、get_file_info、write_file、exec_command。\n"
        "规则：\n"
        "- read-before-write 是硬契约：任何可能覆盖已有文件的 write_file(overwrite=true) 之前，必须先 read_file 同一路径。\n"
        "- 修改已有文件必须先 read_file，再 reasoning 生成新内容，再 write_file(overwrite=true)。\n"
        "- 如果前面用 exec_command 创建了项目或文件，后续覆盖这些新建文件也必须先 read_file 同一路径。\n"
        "- 不允许在 read-before-write 失败后改写无关路径来绕过规则；恢复步骤必须继续使用原 write_file.path。\n"
        "- write_file 的 content 可以使用 content_ref 指向某个 reasoning step。\n"
        "- exec_command 只用于短时非交互终端命令，不用于读写文件。\n"
        "- 不要写入占位内容。\n"
        "- 如果不需要本地工具，返回 {\"needs_task\": false}。\n\n"
        "只返回 JSON，格式：\n"
        "{"
        "\"needs_task\":true,"
        "\"goal\":\"完成用户目标\","
        "\"reason\":\"计划原因\","
        "\"steps\":["
        "{\"id\":\"read_target\",\"kind\":\"tool\",\"description\":\"读取目标文件\","
        "\"tool_name\":\"read_file\",\"arguments\":{\"path\":\"E:/test/config.json\"},\"dependencies\":[]},"
        "{\"id\":\"prepare_content\",\"kind\":\"reasoning\",\"description\":\"基于真实内容生成更新后的完整文本\","
        "\"arguments\":{\"instruction\":\"按用户要求修改内容\"},\"dependencies\":[\"read_target\"]},"
        "{\"id\":\"write_target\",\"kind\":\"tool\",\"description\":\"覆盖写回目标文件\","
        "\"tool_name\":\"write_file\",\"arguments\":{\"path\":\"E:/test/config.json\",\"content_ref\":\"prepare_content\",\"overwrite\":true},"
        "\"dependencies\":[\"prepare_content\"]}"
        "]}\n\n"
        f"history:\n{recent_text or '无'}\n\n"
        f"current:\n{_resolve_user_message(state)}\n\n"
        f"followup_hint:\n{followup_hint or '无'}\n\n"
        f"observations:\n{observations or '暂无'}"
    )


def _task_from_single_action(state: MemoryChatGraphState, action: dict) -> TaskPayload:
    """把旧单 action planner 结果包装成一条 Task，作为迁移期兼容层。"""

    step = _normalize_task_step(
        {
            "id": "step_1",
            "kind": "tool",
            "description": str(action.get("reason") or "执行本地工具。"),
            "tool_name": action.get("tool_name"),
            "arguments": dict(action.get("arguments") or {}),
            "dependencies": [],
        },
        index=0,
    )
    return _new_task_payload(
        base=None,
        state=state,
        goal=_resolve_user_message(state),
        source_user_message=_resolve_user_message(state),
        steps=[step],
        world_state=_empty_world_state(),
        execution_history=[{"type": "planned", "summary": "由兼容单 action planner 生成任务。", "payload": {}}],
        plan_version=1,
        replan_count=0,
    )


def _normalize_task_step(raw_step: dict, *, index: int) -> TaskStepPayload:
    """规整 planner 输出的 Step，保证进入 checkpoint 的结构稳定。"""

    step_id = str(raw_step.get("id") or f"step_{index + 1}")
    kind = str(raw_step.get("kind") or "tool")
    tool_name = raw_step.get("tool_name")
    return {
        "id": re.sub(r"[^A-Za-z0-9_\-]", "_", step_id).strip("_") or f"step_{index + 1}",
        "description": str(raw_step.get("description") or ""),
        "kind": kind,  # type: ignore[typeddict-item]
        "tool_name": str(tool_name) if tool_name else None,
        "arguments": dict(raw_step.get("arguments") or {}),
        "dependencies": [str(item) for item in raw_step.get("dependencies") or []],
        "status": "PENDING",
        "retry_count": int(raw_step.get("retry_count") or 0),
        "output_ref": raw_step.get("output_ref"),
        "attempt_count": int(raw_step.get("attempt_count") or 0),
        "last_error": raw_step.get("last_error"),
        "error": None,
    }


def _select_ready_task_step(task: TaskPayload) -> TaskStepPayload | None:
    """从 pending_steps 取出下一个依赖满足的 step。第一版串行执行。"""

    queued = _task_with_step_queues(task)
    if queued.get("status") == "REPLANNING":
        return None
    completed = {str(step.get("id")) for step in queued.get("completed_steps") or []}
    for step in queued.get("pending_steps") or []:
        if step.get("status") not in {"PENDING", "READY"}:
            continue
        dependencies = [str(item) for item in step.get("dependencies") or []]
        if all(dependency in completed for dependency in dependencies):
            return step
    return None


def _task_step_to_tool_action(step: TaskStepPayload, state: MemoryChatGraphState) -> AgentToolActionPayload | None:
    """把 tool step 转成可复用的 AgentToolActionPayload。"""

    if step.get("kind") != "tool":
        return None
    tool_name = str(step.get("tool_name") or "")
    if tool_name not in READ_TOOL_NAMES | WRITE_TOOL_NAMES | EXEC_TOOL_NAMES:
        return None
    arguments = _resolve_step_arguments(step, state)
    return {
        "tool_name": tool_name,
        "arguments": _clean_tool_path_arguments(tool_name, _normalize_tool_arguments(tool_name, arguments)),
        "reason": str(step.get("description") or ""),
        "source_step_id": str(step.get("id") or ""),
    }


def _resolve_step_arguments(step: TaskStepPayload, state: MemoryChatGraphState) -> dict:
    """解析 step arguments 中的 content_ref 等运行期引用。"""

    arguments = dict(step.get("arguments") or {})
    content_ref = arguments.pop("content_ref", None)
    if content_ref:
        world_state = _resolve_runtime_task(state).get("world_state") or state.get("world_state") or {}
        generated_outputs = dict(world_state.get("generated_outputs") or {})
        generated = dict(generated_outputs.get(str(content_ref)) or {})
        arguments["content"] = str(generated.get("content") or "")
    return arguments


def _generate_reasoning_step_output(step: TaskStepPayload, state: MemoryChatGraphState) -> str:
    """执行 reasoning step，生成后续工具可引用的中间文本。

    第一版先调用回答模型做通用转换；后续可以独立 reasoning model 或结构化 patch 工具。
    """

    prompt = (
        "你是 AiMemo 的任务执行 reasoning step。请基于 world state 和用户目标，"
        "生成该 step 的中间产物。只输出产物正文，不要解释。\n\n"
        f"用户目标：{_resolve_user_message(state)}\n\n"
        f"step：{step.get('description', '')}\n\n"
        f"world_state：{json.dumps(state.get('world_state') or {}, ensure_ascii=False)}"
    )
    try:
        response = get_agent_chat_model().invoke([HumanMessage(content=prompt)])
        return str(response.content)
    except Exception:
        return ""


def _mark_task_step_status(task: TaskPayload, step_id: str, status: str) -> TaskPayload:
    """更新 task 中某个 step 的状态。

    双队列模型下：
      - EXECUTING 只记录 current_step_id，不从 pending 出队。
      - COMPLETED 从 pending 移入 completed。
      - FAILED 从 pending 移入 failed。
    """

    if not task:
        return task
    queued = _task_with_step_queues(task)
    pending: list[TaskStepPayload] = []
    completed = list(queued.get("completed_steps") or [])
    failed = list(queued.get("failed_steps") or [])
    matched: TaskStepPayload | None = None

    for step in queued.get("pending_steps") or []:
        updated = dict(step)
        if str(updated.get("id")) != step_id:
            pending.append(updated)  # type: ignore[arg-type]
            continue
        matched = updated  # type: ignore[assignment]
        if status == "EXECUTING":
            updated["status"] = "PENDING"  # type: ignore[typeddict-item]
            pending.append(updated)  # type: ignore[arg-type]
        elif status == "COMPLETED":
            updated["status"] = "COMPLETED"  # type: ignore[typeddict-item]
            completed.append(updated)  # type: ignore[arg-type]
        elif status == "FAILED":
            updated["status"] = "FAILED"  # type: ignore[typeddict-item]
            failed.append(updated)  # type: ignore[arg-type]
        else:
            updated["status"] = status  # type: ignore[typeddict-item]
            pending.append(updated)  # type: ignore[arg-type]

    if matched is None and status in {"COMPLETED", "FAILED"}:
        for source in (queued.get("completed_steps") or [], queued.get("failed_steps") or []):
            for step in source:
                if str(step.get("id")) == step_id:
                    matched = dict(step)  # type: ignore[assignment]
                    matched["status"] = status  # type: ignore[index]

    updated_task = dict(queued)
    updated_task["pending_steps"] = pending
    updated_task["completed_steps"] = _dedupe_task_steps(completed, keep="last")
    updated_task["failed_steps"] = _dedupe_task_steps(failed, keep="last")
    updated_task["current_step_id"] = step_id if status == "EXECUTING" else None
    updated_task = _task_with_step_queues(updated_task)  # type: ignore[arg-type]
    if status == "FAILED":
        # 失败的当前 step 必须先被 replan，不能让队尾步骤越过它继续执行。
        updated_task["status"] = "REPLANNING"
    return updated_task  # type: ignore[return-value]


def _step_error_from_observation(observation: AgentToolObservationPayload) -> dict | None:
    """把工具 observation 摘成 step.last_error。"""

    if observation.get("ok"):
        return None
    data = _observation_data_dict(observation)
    return {
        "tool_name": observation.get("tool_name"),
        "error_code": observation.get("error_code"),
        "message": observation.get("message"),
        "command": data.get("command"),
        "cwd": data.get("cwd"),
        "exit_code": data.get("exit_code"),
        "stdout_excerpt": _text_excerpt(data.get("stdout")),
        "stderr_excerpt": _text_excerpt(data.get("stderr")),
    }


def _observation_data_dict(observation: AgentToolObservationPayload) -> dict:
    """兼容 dict / JSON 字符串 / Python repr 字符串形式的 observation.data。"""

    data = observation.get("data") or {}
    if isinstance(data, dict):
        return data
    if isinstance(data, str):
        parsed = parse_json_object(data)
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _text_excerpt(value: object, *, limit: int = 1200) -> str:
    """截取适合进入 prompt/debug 的文本片段。"""

    text = str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "\n...[truncated]"


def _derive_task_status(steps: list[TaskStepPayload]) -> str:
    if any(step.get("status") == "FAILED" for step in steps):
        return "REPLANNING"
    if steps and all(step.get("status") == "COMPLETED" for step in steps):
        return "COMPLETED"
    return "RUNNING"


def _task_has_terminal_status(task: TaskPayload) -> bool:
    return task.get("status") in {"COMPLETED", "FAILED", "CANCELLED", "SUPERSEDED"}


def _restore_active_task_for_turn(
    raw_active_task: str,
    *,
    user_message: str,
    recent_messages: list[ChatMessagePayload],
) -> TaskPayload:
    """按当前用户输入恢复上一轮未完成任务。

    参数：
      raw_active_task: Conversation.active_task 中保存的 JSON 字符串。
      user_message: 本轮用户输入。只有“继续/随便你/按你说的”等确认语义才恢复。
      recent_messages: 最近业务消息，用于复用已有的 continuation 判断逻辑。

    返回：
      可继续执行的 Task；如果当前输入是新任务或没有未完成任务，返回空 dict。
    """

    task = _decode_active_task(raw_active_task)
    if not task:
        return {}
    task = _task_with_step_queues(task)
    if _task_has_terminal_status(task) or not task.get("pending_steps"):
        return {}
    if _is_new_tool_task(user_message) and not _looks_like_local_operation_confirmation(user_message):
        return {}
    probe_state: MemoryChatGraphState = {
        "user_message": user_message,
        "recent_messages": recent_messages,
    }
    if not _looks_like_local_operation_confirmation(user_message):
        return {}
    if not _assistant_suggested_local_operation(_latest_assistant_message_text(probe_state)):
        return {}
    restored = deepcopy(task)
    restored["status"] = "READY"  # type: ignore[typeddict-item]
    execution_history = list(restored.get("execution_history") or [])
    execution_history.append(
        {
            "type": "continued_in_new_turn",
            "summary": "用户确认继续上一轮未完成本地任务，已从 conversation.active_task 恢复。",
            "payload": {"user_message": user_message},
        }
    )
    restored["execution_history"] = execution_history  # type: ignore[typeddict-item]
    return restored


def _decode_active_task(raw_active_task: str) -> TaskPayload:
    """解析 Conversation.active_task，兼容空值和历史坏数据。"""

    if not raw_active_task:
        return {}
    try:
        payload = json.loads(raw_active_task)
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _serialize_active_task_for_conversation(state: MemoryChatGraphState) -> str:
    """把未完成任务保存到 Conversation.active_task。

    完成、失败、取消或没有剩余 pending step 时清空。这样下一轮不会误续旧任务；
    只有真实未完成的本地执行目标才会跨 turn 保留。
    """

    task = _task_with_step_queues(state.get("task") or {})
    if not task:
        return "{}"
    if _task_has_terminal_status(task):
        return "{}"
    if not task.get("pending_steps"):
        return "{}"
    return json.dumps(task, ensure_ascii=False)


def _empty_world_state() -> WorldStatePayload:
    return {
        "cwd": None,
        "known_files": {},
        "read_files": {},
        "written_files": {},
        "generated_outputs": {},
        "observations": [],
        "failures": [],
        "replan_debug": [],
        "approvals": [],
    }


def _empty_world_status() -> WorldStatusPayload:
    """构造空任务的 WorldStatus。"""

    return {
        "goal_satisfied": False,
        "missing_requirements": [],
        "requires_replan": False,
        "replan_reason": "",
        "last_error": None,
        "blocked_step_id": None,
        "recovery_hint": "",
        "recovery_path": None,
        "completed_steps": [],
        "failed_steps": [],
        "next_step_id": None,
        "contradictions": [],
        "acceptance_summary": [],
    }


def _verify_task_goal(
    task: TaskPayload,
    world_state: WorldStatePayload,
    state: MemoryChatGraphState,
) -> GoalVerificationPayload:
    """基于真实工具事实验收当前 Task 是否满足用户目标。

    参数：
      task: 当前 Dynamic Execution Task。
      world_state: 工具调用沉淀出的事实状态。
      state: 当前 graph state，用于读取原始用户输入。

    返回：
      GoalVerificationPayload。`satisfied=true` 才允许成功式最终回答。
    """

    goal_text = f"{task.get('goal') or ''}\n{_resolve_user_message(state)}"
    observations = list(world_state.get("observations") or [])
    missing: list[str] = []
    contradictions: list[str] = []
    evidence: list[dict] = []

    if _goal_requires_exec_result(goal_text):
        exec_observation = _latest_successful_exec_observation(observations)
        if exec_observation is None:
            missing.append("缺少成功的命令执行结果。")
        else:
            data = _observation_data_dict(exec_observation)  # type: ignore[arg-type]
            stdout = str(data.get("stdout") or "")
            stderr = str(data.get("stderr") or "")
            evidence.append(
                {
                    "source": "tool_observation",
                    "tool_call_id": exec_observation.get("tool_call_id"),
                    "step_id": _step_id_from_tool_call_id(str(exec_observation.get("tool_call_id") or "")),
                    "tool_name": "exec_command",
                    "field": "stdout",
                    "value_excerpt": _text_excerpt(stdout or stderr),
                }
            )
            output_problem = _exec_output_contradiction(goal_text, stdout, stderr)
            if output_problem:
                contradictions.append(output_problem)

    satisfied = not missing and not contradictions
    if satisfied:
        reason = "目标验收通过，已有工具事实支撑最终回答。"
    else:
        reason_parts = [*missing, *contradictions]
        reason = "目标验收未通过：" + "；".join(reason_parts)
    return {
        "satisfied": satisfied,
        "reason": reason,
        "missing_criteria": missing,
        "contradictions": contradictions,
        "evidence": evidence,
    }


def _world_status_from_verification(
    world_status: WorldStatusPayload,
    verification: GoalVerificationPayload,
) -> WorldStatusPayload:
    """把目标验收结果合并回 WorldStatus，供 agent_think 触发 replan。"""

    updated = dict(world_status)
    if verification.get("satisfied"):
        updated["goal_satisfied"] = True
        updated["requires_replan"] = False
        updated["replan_reason"] = ""
        updated["missing_requirements"] = []
        updated["contradictions"] = []
    else:
        updated["goal_satisfied"] = False
        updated["requires_replan"] = True
        updated["replan_reason"] = str(verification.get("reason") or "目标验收未通过，需要重规划。")
        updated["missing_requirements"] = list(verification.get("missing_criteria") or [])
        updated["contradictions"] = list(verification.get("contradictions") or [])
    updated["acceptance_summary"] = [
        {
            "satisfied": bool(verification.get("satisfied")),
            "reason": verification.get("reason", ""),
            "evidence_count": len(verification.get("evidence") or []),
        }
    ]
    return updated  # type: ignore[return-value]


def _latest_successful_exec_observation(observations: list[dict]) -> dict | None:
    """读取最近一次成功 exec_command observation。"""

    for observation in reversed(observations):
        if observation.get("tool_name") != "exec_command" or not observation.get("ok"):
            continue
        data = _observation_data_dict(observation)  # type: ignore[arg-type]
        if int(data.get("exit_code", 0) or 0) == 0:
            return observation
    return None


def _exec_output_contradiction(goal_text: str, stdout: str, stderr: str) -> str:
    """检查命令输出是否明显违背目标。

    第一版只做高置信规则：用户明确要求随机数时，输出必须包含足够数量的数字。
    其他目标先不做过度猜测，避免把通用能力写成语言/框架特化。
    """

    output = f"{stdout}\n{stderr}".strip()
    if not output:
        return "命令执行成功但没有输出，无法作为运行结果返回。"
    expected_numbers = _expected_random_number_count(goal_text)
    if expected_numbers:
        numbers = re.findall(r"(?<![A-Za-z0-9_])-?\d+(?:\.\d+)?(?![A-Za-z0-9_])", output)
        if len(numbers) < expected_numbers:
            return f"用户目标要求生成 {expected_numbers} 个随机数，但命令输出中只识别到 {len(numbers)} 个数字。"
    return ""


def _expected_random_number_count(text: str) -> int:
    """从用户目标中提取“生成 N 个随机数”的验收数量。"""

    if "随机数" not in text and "random" not in text.lower():
        return 0
    match = re.search(r"(\d+)\s*个?\s*(?:随机数|random\s+numbers?)", text, flags=re.IGNORECASE)
    return int(match.group(1)) if match else 0


def _step_id_from_tool_call_id(tool_call_id: str) -> str | None:
    """从 tool_call_id 中尽量还原 step_id。

    旧格式可能是 `tool-1-exec_command`，没有 step 信息时返回 None。
    """

    return None


def _evaluate_world_status(
    task: TaskPayload,
    world_state: WorldStatePayload,
    state: MemoryChatGraphState,
) -> WorldStatusPayload:
    """基于 task + world_state 判断当前任务进展。

    WorldState 是事实日志，WorldStatus 是执行判断层。第一版先做确定性判断：
      - 尚未解决的失败 observation 会触发 requires_replan；failed_steps 仅保留历史。
      - 用户目标包含“运行/结果/测试/验证”等意图时，必须看到成功 exec observation。
      - completed_steps/failed_steps/next_step_id 用于前端调试和 agent_think 决策。
    后续可以把缺失要求判断升级为 LLM evaluator，但底层事实仍来自这里。
    """

    task = _task_with_step_queues(task)
    completed_steps = [str(step.get("id")) for step in task.get("completed_steps") or []]
    failed_steps = [str(step.get("id")) for step in task.get("failed_steps") or []]
    failures = list(world_state.get("failures") or [])
    active_failures = _active_failures_for_world_state(failures, world_state)
    invalid_plan_error = _invalid_plan_error(task)
    last_error = invalid_plan_error or (active_failures[-1] if active_failures else None)
    next_step = _select_ready_task_step(task)
    blocked_step_id = str(last_error.get("step_id") or "") if last_error else ""
    recovery_hint = str(last_error.get("recovery_hint") or "") if last_error else ""
    recovery_path = str(last_error.get("path") or "") if last_error else ""
    missing_requirements: list[str] = []
    user_goal = f"{task.get('goal') or ''}\n{_resolve_user_message(state)}"
    observations = list(world_state.get("observations") or [])

    if _goal_requires_exec_result(user_goal) and not _has_successful_exec_observation(observations):
        missing_requirements.append("需要成功执行命令并获得运行结果。")

    requires_replan = bool(invalid_plan_error or active_failures or missing_requirements)
    replan_reason = ""
    if invalid_plan_error:
        replan_reason = str(invalid_plan_error.get("message") or "动态任务计划不可执行，需要重新规划。")
    elif active_failures:
        failed_label = str(last_error.get("step_id") or "工具调用") if last_error else "工具调用"
        replan_reason = f"{failed_label} 执行失败，需要基于错误结果重新规划当前或后续步骤。"
    elif missing_requirements:
        replan_reason = "任务目标仍缺少必要结果，需要补充后续步骤。"

    terminal_completed = task.get("status") == "COMPLETED"
    goal_satisfied = terminal_completed and not missing_requirements and not requires_replan
    return {
        "goal_satisfied": goal_satisfied,
        "missing_requirements": missing_requirements,
        "requires_replan": requires_replan,
        "replan_reason": replan_reason,
        "last_error": last_error,
        "blocked_step_id": blocked_step_id or None,
        "recovery_hint": recovery_hint,
        "recovery_path": recovery_path or None,
        "completed_steps": completed_steps,
        "failed_steps": failed_steps,
        "next_step_id": str(next_step.get("id")) if next_step else None,
    }


def _invalid_plan_error(task: TaskPayload) -> dict | None:
    """检测 pending 非空但当前计划不可执行的结构错误。"""

    queued = _task_with_step_queues(task)
    pending = list(queued.get("pending_steps") or [])
    if not pending or queued.get("status") == "REPLANNING":
        return None
    if _select_ready_task_step(queued) is not None:
        return None
    completed = {str(step.get("id")) for step in queued.get("completed_steps") or []}
    known_steps = completed | {str(step.get("id")) for step in pending} | {str(step.get("id")) for step in queued.get("failed_steps") or []}
    for step in pending:
        for dependency in [str(item) for item in step.get("dependencies") or []]:
            if dependency not in known_steps:
                return {
                    "step_id": str(step.get("id") or ""),
                    "tool_name": step.get("tool_name"),
                    "error_code": "INVALID_PLAN_DEPENDENCY",
                    "message": f"动态计划中的 step `{step.get('id')}` 依赖不存在的 `{dependency}`。",
                    "missing_dependency": dependency,
                    "recovery_hint": "replace_pending_plan",
                }
    return {
        "step_id": str(pending[0].get("id") or ""),
        "tool_name": pending[0].get("tool_name"),
        "error_code": "NO_READY_STEP",
        "message": "pending_steps 非空，但没有依赖满足的可执行 step。",
        "recovery_hint": "replace_pending_plan",
    }


def _active_failures_for_world_state(
    failures: list[dict],
    world_state: WorldStatePayload,
) -> list[dict]:
    """过滤已经被后续 observation 解决的失败。

    例如 READ_BEFORE_WRITE_REQUIRED 后，如果同一路径已经 read_file 成功且 write_file
    覆盖成功，这个失败就不再是 active failure，不能继续驱动 replan。
    """

    active: list[dict] = []
    for failure in failures:
        if _is_failure_resolved(failure, world_state):
            continue
        active.append(failure)
    return active


def _is_failure_resolved(failure: dict, world_state: WorldStatePayload) -> bool:
    """判断某条失败是否已被后续工具结果解决。"""

    if failure.get("recovery_hint") == "read_then_write_same_path":
        path = str(failure.get("path") or "")
        if not path:
            return False
        read_files = dict(world_state.get("read_files") or {})
        written_files = dict(world_state.get("written_files") or {})
        return path in read_files and path in written_files
    if failure.get("tool_name") == "exec_command" and failure.get("error_code") == "COMMAND_EXITED_NON_ZERO":
        return _has_successful_exec_observation(list(world_state.get("observations") or []))
    return False


def _goal_requires_exec_result(text: str) -> bool:
    """判断用户目标是否要求实际运行、测试、验证或返回命令结果。"""

    lowered = str(text or "").lower()
    markers = [
        "运行结果",
        "执行结果",
        "跑一下",
        "运行",
        "执行",
        "测试",
        "验证",
        "run",
        "execute",
        "test",
    ]
    return any(marker in lowered for marker in markers)


def _has_successful_exec_observation(observations: list[dict]) -> bool:
    """检查是否已经有成功的 exec_command observation。"""

    for observation in observations:
        if observation.get("tool_name") != "exec_command" or not observation.get("ok"):
            continue
        data = dict(observation.get("data") or {})
        if int(data.get("exit_code", 1) or 0) == 0:
            return True
    return False


def _task_id_from_user_message(user_message: str) -> str:
    return f"task-{abs(hash(user_message)) % 1_000_000_000}"


def _update_task_after_tool_observation(
    state: MemoryChatGraphState,
    observation: AgentToolObservationPayload,
) -> TaskPayload:
    """工具返回后更新 Task step 状态和 WorldState。"""

    task = _task_with_step_queues(state.get("task") or {})
    if not task:
        return task  # type: ignore[return-value]
    action = state.get("pending_tool_action") or {}
    step_id = str(action.get("source_step_id") or action.get("tool_call_id") or "")
    if not step_id:
        return task  # type: ignore[return-value]
    if not observation.get("ok"):
        task = _attach_last_error_to_pending_step(task, step_id, _step_error_from_observation(observation))
    updated_task = _mark_task_step_status(task, step_id, "COMPLETED" if observation.get("ok") else "FAILED")
    world_state = _update_world_state_after_tool_observation(
        updated_task.get("world_state") or _empty_world_state(),
        observation,
        step_id=step_id,
    )
    updated_task["world_state"] = world_state
    history = list(updated_task.get("execution_history") or [])
    history.append(
        {
            "type": "step_completed" if observation.get("ok") else "step_failed",
            "step_id": step_id,
            "summary": _summarize_tool_observation(observation),
            "payload": observation,
        }
    )
    updated_task["execution_history"] = history
    return updated_task  # type: ignore[return-value]


def _attach_last_error_to_pending_step(
    task: TaskPayload,
    step_id: str,
    error: dict | None,
) -> TaskPayload:
    """在 step 移入 failed 前写入 last_error。"""

    if not error:
        return task
    queued = _task_with_step_queues(task)
    pending: list[TaskStepPayload] = []
    for step in queued.get("pending_steps") or []:
        updated = dict(step)
        if str(updated.get("id")) == step_id:
            updated["last_error"] = error  # type: ignore[typeddict-item]
            updated["error"] = error  # type: ignore[typeddict-item]
            updated["attempt_count"] = int(updated.get("attempt_count") or 0) + 1  # type: ignore[typeddict-item]
        pending.append(updated)  # type: ignore[arg-type]
    updated_task = dict(queued)
    updated_task["pending_steps"] = pending
    return _task_with_step_queues(updated_task)  # type: ignore[arg-type]


def _update_world_state_after_tool_observation(
    world_state: WorldStatePayload,
    observation: AgentToolObservationPayload,
    *,
    step_id: str = "",
) -> WorldStatePayload:
    """把工具 observation 写入 WorldState。"""

    updated = dict(world_state or _empty_world_state())
    observations = list(updated.get("observations") or [])
    observations.append(dict(observation))
    updated["observations"] = observations
    if not observation.get("ok"):
        failures = list(updated.get("failures") or [])
        failure = _step_error_from_observation(observation) or {}
        arguments = observation.get("arguments") or {}
        failures.append(
            {
                "step_id": step_id,
                "tool_name": observation.get("tool_name"),
                "error_code": observation.get("error_code"),
                "message": observation.get("message"),
                "path": arguments.get("path") if isinstance(arguments, dict) else None,
                "recovery_hint": _recovery_hint_for_tool_failure(observation),
                "command": failure.get("command"),
                "cwd": failure.get("cwd"),
                "exit_code": failure.get("exit_code"),
                "stdout_excerpt": failure.get("stdout_excerpt"),
                "stderr_excerpt": failure.get("stderr_excerpt"),
            }
        )
        updated["failures"] = failures
        return updated  # type: ignore[return-value]

    data = dict(observation.get("data") or {})
    tool_name = observation.get("tool_name")
    if tool_name == "read_file":
        read_files = dict(updated.get("read_files") or {})
        path = str(data.get("path") or data.get("relative_path") or "")
        if path:
            read_files[path] = {
                "content": data.get("content") or "",
                "content_hash": data.get("content_hash") or "",
                "line_start": data.get("line_start"),
                "line_end": data.get("line_end"),
            }
            updated["read_files"] = read_files
    elif tool_name == "write_file":
        written_files = dict(updated.get("written_files") or {})
        path = str(data.get("path") or data.get("relative_path") or "")
        if path:
            written_files[path] = {
                "content_hash": data.get("content_hash") or "",
                "bytes_written": data.get("bytes_written"),
                "type": data.get("type"),
            }
            updated["written_files"] = written_files
    elif tool_name == "exec_command":
        updated["cwd"] = data.get("cwd") or updated.get("cwd")
    return updated  # type: ignore[return-value]


def _recovery_hint_for_tool_failure(observation: AgentToolObservationPayload) -> str:
    """把确定性工具错误映射成恢复提示。"""

    if observation.get("tool_name") == "write_file" and observation.get("error_code") == "READ_BEFORE_WRITE_REQUIRED":
        return "read_then_write_same_path"
    return ""


def _update_world_state_for_reasoning(
    world_state: WorldStatePayload,
    step: TaskStepPayload,
    content: str,
) -> WorldStatePayload:
    """把 reasoning step 生成的中间产物写入 WorldState。"""

    updated = dict(world_state or _empty_world_state())
    generated_outputs = dict(updated.get("generated_outputs") or {})
    step_id = str(step.get("id") or "")
    generated_outputs[step_id] = {
        "content": content,
        "description": step.get("description") or "",
    }
    updated["generated_outputs"] = generated_outputs
    return updated  # type: ignore[return-value]


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
    if tool_name not in READ_TOOL_NAMES | WRITE_TOOL_NAMES | EXEC_TOOL_NAMES:
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
        "- 如果用户要求运行测试、查看版本、执行构建、查看 git 状态，应该选择 exec_command。\n"
        "- exec_command 不用于读取/写入/搜索文件；这些需求必须选择专用文件工具。\n"
        "- 只有当本轮只是确认上一轮草稿，比如“你自己取文件名/按你说的写/就这样保存”时，才沿用上一轮目录和正文补文件名并 write_file。\n"
        "- 如果 get_file_info 返回某个目标文件 PATH_NOT_FOUND，而用户本意是创建文件，不要把它解释成目录不存在；下一步应考虑 write_file。\n"
        "- write_file 的 content 必须是真实正文，禁止写入“此处填写”“待补充”“TODO”等占位模板。\n"
        "- 工具 observation 只说明某一步已经完成或失败，不能单独决定整个任务终止。\n"
        "- write_file 成功后，如果当前目标还要求运行、编译、测试、验证或返回运行结果，必须继续选择 exec_command。\n"
        "- 只有当用户目标中的所有要求都已经被 observation 覆盖时，才可以返回 needs_tool=false 并进入最终回答。\n"
        "- 不要因为路径在 C/D/E 盘或 Home 外就拒绝规划；路径策略由工具执行层判断。\n\n"
        "可用工具：list_dir、read_file、search_files、search_text、get_file_info、write_file、exec_command。\n"
        "exec_command 只用于短时、非交互终端命令，例如运行测试、查看版本、git status；不要用它读写文件。\n"
        "只返回 JSON，不要输出其他文本。格式：\n"
        "{"
        "\"needs_tool\":true,"
        "\"tool_name\":\"exec_command\","
        "\"arguments\":{\"command\":\"git status --short\",\"cwd\":\".\",\"timeout_ms\":30000,\"max_output_bytes\":65536},"
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
    if _looks_like_exec_request(user_message):
        return True
    if state.get("tool_observations"):
        return True
    return _looks_like_agent_choose_filename_request(user_message)


def _tool_planner_input(state: MemoryChatGraphState) -> str:
    """构造本地工具 planner 输入。

    轻量 LLM planner 不能只看本轮 `user_message`。当用户说“按刚才那个文件保存”
    或“继续写进去”时，真正的 path/content 往往在上一轮 assistant 中。
    这里明确标记 history/current 边界，避免工具规划器把上一轮 assistant 的草稿
    当成本轮用户指令继续执行。
    """

    recent_text = "\n".join(
        f"{message.get('role')}: {message.get('content')}"
        for message in state.get("recent_messages", [])[-6:]
    )
    current = _resolve_user_message(state)
    return (
        "## history\n"
        f"{recent_text or '无历史消息。'}\n\n"
        "## current\n"
        f"user: {current}"
    ).strip()


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
    if _is_new_tool_task(user_message):
        return None
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
        if not write_content and _looks_like_agent_choose_filename_request(user_message):
            write_content = _extract_contextual_write_content(content, "")
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


def _is_new_tool_task(user_message: str) -> bool:
    """判断当前输入是否已经形成新的本地工具任务。

    如果本轮用户给了明确路径、读取/修改/执行等动作，就不能再沿用历史 assistant
    草稿。这是防止 continuation over-trigger 的第一道任务边界。
    """

    has_path = bool(_extract_path(user_message))
    new_task_keywords = [
        "读取",
        "读一下",
        "查看",
        "搜索",
        "查找",
        "修改",
        "改成",
        "替换",
        "保存回去",
        "执行命令",
        "运行命令",
        "测试",
    ]
    return has_path and any(keyword in user_message for keyword in new_task_keywords)


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
    if tool_name == "exec_command" and "cwd" in cleaned:
        cleaned["cwd"] = _clean_tool_path(str(cleaned.get("cwd") or "."))
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
        "是否",
        "希望我",
        "你希望",
        "比如",
        "例如",
        normalized_path,
    ]
    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            if kept and kept[-1] != "":
                kept.append("")
            continue
        if any(marker and marker in line for marker in skip_markers):
            continue
        if _looks_like_contextual_write_operation_line(line):
            continue
        kept.append(line)
    content = "\n".join(kept).strip()
    return content if len(content) >= 8 else ""


def _looks_like_contextual_write_operation_line(line: str) -> bool:
    """判断 assistant 文本中的某一行是否只是保存/命名操作说明。

    不能简单跳过包含“文件”的所有句子，因为正文里也可能自然提到文件、项目、代码。
    这里只过滤“你希望/如果你愿意/我可以帮你...”这类询问或操作性句子。
    """

    operation_markers = ["取一个文件名", "取文件名", "文件名", "写进去", "保存到", "保存成", "直接保存", "保存位置"]
    intent_markers = ["如果你愿意", "你希望", "希望我", "我可以帮你", "是否", "要不要", "我确认"]
    return any(marker in line for marker in operation_markers) and any(marker in line for marker in intent_markers)


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


def _requires_local_operation_followup(state: MemoryChatGraphState) -> bool:
    """判断当前输入是否是在确认继续上一轮本地工具操作。

    参数：
      state: 当前 graph state，主要读取 current user_message 与 recent_messages。

    返回：
      True 表示本轮虽然没有明确路径/命令，但结合上一轮 assistant 的本地操作建议，
      应进入工具规划；False 表示普通聊天或新任务。
    """

    user_message = _resolve_user_message(state)
    if _is_new_tool_task(user_message) or _should_try_llm_tool_planner(user_message):
        return False
    if not _looks_like_local_operation_confirmation(user_message):
        return False
    assistant = _latest_assistant_message_text(state)
    return _assistant_suggested_local_operation(assistant)


def _requires_exec_result_followup(state: MemoryChatGraphState) -> bool:
    """判断当前输入或上一轮上下文是否要求真实命令运行结果。"""

    user_message = _resolve_user_message(state)
    if _goal_requires_exec_result(user_message):
        return True
    if not _looks_like_local_operation_confirmation(user_message):
        return False
    assistant = _latest_assistant_message_text(state)
    return _assistant_suggested_local_operation(assistant) and _goal_requires_exec_result(assistant)


def _local_operation_followup_hint(state: MemoryChatGraphState) -> str:
    """为 planner 提供跨轮确认的结构化提示。"""

    if not _requires_local_operation_followup(state):
        return ""
    assistant = _latest_assistant_message_text(state)
    return (
        "current 看起来是在确认上一轮 assistant 的本地操作建议。"
        "不要直接回答已完成；请规划真实工具步骤。最近 assistant 内容如下：\n"
        f"{assistant[-1800:]}"
    )


def _looks_like_local_operation_confirmation(text: str) -> bool:
    """识别“可以，继续/覆盖/按你说的”这类短确认。"""

    normalized = str(text or "").strip()
    if len(normalized) > 80:
        return False
    confirmation_keywords = ["可以", "好", "好的", "行", "嗯", "继续", "直接", "按你说的", "就这样", "覆盖", "重试"]
    operation_keywords = ["继续", "直接", "覆盖", "保存", "写", "运行", "执行", "重试", "修正", "修改", "按你说的"]
    return any(keyword in normalized for keyword in confirmation_keywords) and any(
        keyword in normalized for keyword in operation_keywords
    )


def _latest_assistant_message_text(state: MemoryChatGraphState) -> str:
    """读取最近一条 assistant 消息内容。"""

    for message in reversed(state.get("recent_messages", [])):
        if message.get("role") == "assistant":
            return str(message.get("content") or "")
    return ""


def _assistant_suggested_local_operation(text: str) -> bool:
    """判断上一轮 assistant 是否明确提出了本地文件/命令操作。"""

    if not text:
        return False
    local_markers = [
        "Cargo.toml",
        "write_file",
        "exec_command",
        "cargo run",
        "覆盖写入",
        "直接覆盖",
        "重新运行",
        "运行结果",
        "编译",
        "保存",
        "写入",
        "本地",
        "文件",
        "命令",
    ]
    proposal_markers = ["需要我", "你希望我", "我需要", "我可以", "建议", "尝试", "继续", "直接"]
    return any(marker in text for marker in local_markers) and any(marker in text for marker in proposal_markers)


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
        "读取",
        "查看",
        "修改",
        "改成",
        "替换",
        "保存回去",
        "写回",
        "写入",
        "创建",
        "保存到",
        "执行",
        "运行",
        "测试",
        "命令",
        "终端",
        "git status",
        "npm",
        "pytest",
        "cargo",
        "Ai记",
        "Ai 记",
        "AiMemo",
    ]
    return any(keyword in user_message for keyword in keywords)


def _looks_like_exec_request(user_message: str) -> bool:
    """识别明显需要终端命令的用户输入。"""

    exec_keywords = ["执行命令", "运行命令", "跑一下", "启动", "测试", "构建", "git status", "npm", "pytest", "cargo", "python --version"]
    return any(keyword in user_message for keyword in exec_keywords)


def _to_agent_tool_action(
    action: dict,
    *,
    index: int,
    task_boundary: Literal["new_task", "continuation", "same_turn_followup"] = "new_task",
) -> AgentToolActionPayload:
    """把 Local Operator action 规整为主对话 graph 的工具 action。"""

    tool_name = str(action.get("tool_name") or "")
    if tool_name in WRITE_TOOL_NAMES:
        operation_type = "write"
    elif tool_name in EXEC_TOOL_NAMES:
        operation_type = "exec"
    else:
        operation_type = "read"
    return {
        "tool_call_id": f"tool-{index + 1}-{tool_name or 'unknown'}",
        "tool_name": tool_name,
        "arguments": dict(action.get("arguments") or {}),
        "reason": str(action.get("reason") or ""),
        "source_step_id": str(action.get("source_step_id") or ""),
        "operation_type": operation_type,
        "risk_level": "medium" if operation_type in {"write", "exec"} else "low",
        "requires_approval": False,
        "status": "READY",
        "task_boundary": task_boundary,
    }


def _infer_task_boundary(
    state: MemoryChatGraphState,
    action: dict,
) -> Literal["new_task", "continuation", "same_turn_followup"]:
    """给工具 action 标记任务边界，便于 checkpoint/debug 排查跨轮串状态。

    当前还没有独立 TaskSession 表，先把边界信息压进 action payload：
      - continuation: 用户明确确认上一轮 assistant 草稿。
      - same_turn_followup: 已有 observation 后继续同一轮工具链。
      - new_task: 当前 L0 输入形成的新工具任务。
    """

    reason = str(action.get("reason") or "")
    if state.get("tool_observations"):
        return "same_turn_followup"
    if "上一轮 assistant" in reason or "上一轮" in reason:
        return "continuation"
    return "new_task"


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

    updated_task = _update_task_after_tool_observation(state, observation)
    updated_world_state = updated_task.get("world_state") or _empty_world_state()
    return {
        "tool_observations": [*state.get("tool_observations", []), observation],
        "tool_budget": max(int(state.get("tool_budget") or 0) - 1, 0),
        "pending_tool_action": {
            **action,
            "status": "COMPLETED" if observation.get("ok") else "FAILED",
        },
        "task": updated_task,
        # 顶层 world_state 是给调试 UI/后续节点快速读取的镜像，必须和 task.world_state
        # 使用同一份更新结果，不能再从旧 state 二次推导，否则 step 状态和观察事实会脱节。
        "world_state": updated_world_state,
        "world_status": _evaluate_world_status(updated_task, updated_world_state, state),
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
            "error_detail": _step_error_from_observation(observation),
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
