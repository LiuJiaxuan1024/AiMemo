from collections.abc import Callable
from contextlib import AbstractContextManager

from sqlmodel import Session, desc, select

from app.agent.graphs.memory_chat.persistence import _to_message_payload
from app.agent.graphs.memory_chat.state import MemoryChatGraphState
from app.agent.graphs.memory_chat.task_world import _empty_world_state
from app.models.chat_message import ChatMessage
from app.models.conversation import Conversation


SessionFactory = Callable[[], AbstractContextManager[Session]]


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
                "context_lx_web_layer": {},
                "web_search_debug": {},
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






