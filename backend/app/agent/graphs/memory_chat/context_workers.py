from collections.abc import Callable
from contextlib import AbstractContextManager

from langgraph.types import Send
from sqlmodel import Session

from app.agent.context import (
    ContextBudget,
    build_adjacent_turn_layer,
    build_core_memory_layer,
    build_current_conversation_window_layer,
    build_current_input_layer,
    build_recent_messages_layer,
    build_summary_layer,
)
from app.agent.graphs.memory_chat.state import MemoryChatGraphState
from app.core.config import settings
from app.services.long_term_memory_service import format_core_memory_with_sources_for_prompt, list_core_memories


SessionFactory = Callable[[], AbstractContextManager[Session]]


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
        Send("build_lx_web_context", state),
        Send("build_l0_adjacent_turn", state),
        Send("build_l0_current_input", state),
        Send("build_current_conversation_window", state),
    ]


def build_l4_core_memory_node(session_factory: SessionFactory):
    """构建 L4 核心长期记忆层。"""

    def build_l4_core_memory(state: MemoryChatGraphState) -> MemoryChatGraphState:
        with session_factory() as session:
            core_memories = [
                format_core_memory_with_sources_for_prompt(session, memory)
                for memory in list_core_memories(session)
            ]
        layer = build_core_memory_layer(core_memories, _context_budget())
        return {"context_l4_layer": layer.to_payload()}

    return build_l4_core_memory


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


def _context_budget() -> ContextBudget:
    return settings.context_pyramid_budget


def _resolve_user_message(state: MemoryChatGraphState) -> str:
    user_message = state.get("user_message", "").strip()
    if not user_message:
        raise ValueError("user_message is required.")
    return user_message
