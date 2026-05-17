import logging
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

from langchain_core.messages import HumanMessage, SystemMessage
from sqlmodel import Session, select

from app.ai.json_utils import parse_json_object
from app.agent.graphs.conversation_memory.state import ConversationMemoryGraphState
from app.agent.graphs.memory_chat.state import ChatMessagePayload
from app.agent.model import get_agent_chat_model
from app.models.chat_message import ChatMessage
from app.models.conversation import Conversation
from app.models.long_term_memory import LongTermMemory
from app.models.note import utc_now
from app.services.memory_service import build_memory_content_hash


SessionFactory = Callable[[], AbstractContextManager[Session]]
MemoryExtractor = Callable[[list[ChatMessagePayload]], dict[str, Any]]

MIN_IMPORTANCE = 0.7
MIN_CONFIDENCE = 0.6

logger = logging.getLogger(__name__)


def build_load_memory_source_node(session_factory: SessionFactory):
    """读取需要进行长期记忆抽取的一轮对话消息。

    第一版只处理一问一答：用户消息和对应的助手消息。这样触发边界清晰，
    后续要扩展为多消息窗口时，可以只调整 payload 和本节点读取逻辑。
    """

    def load_memory_source(
        state: ConversationMemoryGraphState,
    ) -> ConversationMemoryGraphState:
        conversation_id = _resolve_int(state, "conversation_id")
        user_message_id = _resolve_int(state, "user_message_id")
        assistant_message_id = _resolve_int(state, "assistant_message_id")

        with session_factory() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                raise ValueError(f"Conversation {conversation_id} not found.")

            user = _get_message(session, conversation_id, user_message_id)
            assistant = _get_message(session, conversation_id, assistant_message_id)
            return {
                "conversation_id": conversation_id,
                "user_message_id": user_message_id,
                "assistant_message_id": assistant_message_id,
                "source_messages": [_to_message_payload(user), _to_message_payload(assistant)],
            }

    return load_memory_source


def build_extract_memories_node(memory_extractor: MemoryExtractor | None = None):
    """调用模型判断本轮对话是否包含值得长期记住的信息。"""

    def extract_memories(
        state: ConversationMemoryGraphState,
    ) -> ConversationMemoryGraphState:
        messages = state.get("source_messages", [])
        if not messages:
            raise ValueError("source_messages is required before memory extraction.")
        extractor = memory_extractor or extract_long_term_memories
        return {"extraction_result": extractor(messages)}

    return extract_memories


def build_write_memories_node(session_factory: SessionFactory):
    """把高质量抽取结果写入 longtermmemory。

    写入规则保持保守：
      - should_write 必须为 true。
      - importance/confidence 必须超过阈值。
      - content_hash 已存在时跳过，避免重复记忆。
    """

    def write_memories(
        state: ConversationMemoryGraphState,
    ) -> ConversationMemoryGraphState:
        result = state.get("extraction_result") or {}
        memories = result.get("memories", [])
        if not isinstance(memories, list):
            raise ValueError("extraction_result.memories must be a list.")

        source_id = _resolve_int(state, "assistant_message_id")
        written_ids: list[int] = []
        with session_factory() as session:
            for raw_memory in memories:
                normalized = _normalize_memory(raw_memory)
                if normalized is None:
                    continue
                memory_hash = build_memory_content_hash(
                    normalized["category"],
                    normalized["content"],
                )
                existing = session.exec(
                    select(LongTermMemory).where(LongTermMemory.content_hash == memory_hash)
                ).first()
                if existing:
                    continue

                memory = LongTermMemory(
                    level=4,
                    category=normalized["category"],
                    content=normalized["content"],
                    summary=normalized["summary"],
                    importance=normalized["importance"],
                    confidence=normalized["confidence"],
                    source_type="chat_message",
                    source_id=source_id,
                    status="active",
                    content_hash=memory_hash,
                    updated_at=utc_now(),
                )
                session.add(memory)
                session.flush()
                if memory.id is not None:
                    written_ids.append(memory.id)
            session.commit()
        return {"written_memory_ids": written_ids}

    return write_memories


def extract_long_term_memories(messages: list[ChatMessagePayload]) -> dict[str, Any]:
    """使用 qwen3.5-plus 抽取长期核心记忆候选。"""

    messages_text = "\n".join(
        f"{message['role']}: {message['content']}" for message in messages
    )
    prompt = (
        "你是 Ai 记的长期记忆抽取器。判断以下一轮对话中是否有值得长期记住的信息。\n"
        "只返回严格合法 JSON，不要输出 markdown，不要输出解释文本。JSON 格式：\n"
        "{"
        "\"memories\":["
        "{"
        "\"should_write\":true,"
        "\"category\":\"preference|identity|goal|instruction|event|fact\","
        "\"content\":\"用一句中文写成稳定长期记忆\","
        "\"summary\":\"更短的摘要\","
        "\"importance\":0.0,"
        "\"confidence\":0.0,"
        "\"reason\":\"简短原因\""
        "}"
        "]"
        "}\n\n"
        "判断原则：\n"
        "- 只保留未来多次对话仍有帮助的信息，例如稳定偏好、身份、长期目标、长期指令。\n"
        "- 临时闲聊、一次性问题、模型自己的回答、不确定猜测不要写入。\n"
        "- 如果没有值得记住的信息，返回 {\"memories\":[]}。\n\n"
        "格式要求：所有 key 和字符串必须使用英文双引号；字段之间必须有英文逗号；"
        "不要出现尾随逗号。\n\n"
        f"对话：\n{messages_text}"
    )
    # DashScope OpenAI-compatible 接口支持 JSON mode。长期记忆抽取对格式稳定性要求高，
    # 这里用 response_format 从源头降低未转义引号、markdown 包裹等非严格 JSON 输出概率。
    json_model = get_agent_chat_model().bind(response_format={"type": "json_object"})
    response = json_model.invoke(
        [
            SystemMessage(content="你负责为个人知识库抽取高价值长期记忆。"),
            HumanMessage(content=prompt),
        ]
    )
    return parse_memory_extraction_response(str(response.content))


def parse_memory_extraction_response(text: str) -> dict[str, Any]:
    """解析长期记忆抽取结果。

    长期记忆抽取是后台增强任务，不应该因为模型偶发输出了非严格 JSON 而让 job
    失败并反复重试。解析失败时降级为“不写入记忆”，并把原始片段写入日志供调试。
    """

    try:
        payload = parse_json_object(text)
    except Exception as exc:  # noqa: BLE001 - 后台 job 需要兜住模型格式异常。
        logger.warning(
            "conversation_memory.parse_failed error=%r response_preview=%r",
            exc,
            text[:500],
        )
        return {"memories": []}

    memories = payload.get("memories", [])
    if not isinstance(memories, list):
        logger.warning(
            "conversation_memory.invalid_memories_type type=%s response_preview=%r",
            type(memories).__name__,
            text[:500],
        )
        return {"memories": []}
    return {"memories": memories}


def _normalize_memory(raw_memory: Any) -> dict[str, Any] | None:
    if not isinstance(raw_memory, dict):
        return None
    if not bool(raw_memory.get("should_write", False)):
        return None

    content = str(raw_memory.get("content") or "").strip()
    if not content:
        return None
    importance = _clamp_float(raw_memory.get("importance", 0.0))
    confidence = _clamp_float(raw_memory.get("confidence", 0.0))
    if importance < MIN_IMPORTANCE or confidence < MIN_CONFIDENCE:
        return None

    category = str(raw_memory.get("category") or "fact").strip().lower()
    if category not in {"preference", "identity", "goal", "instruction", "event", "fact"}:
        category = "fact"
    summary = str(raw_memory.get("summary") or content).strip()
    return {
        "category": category,
        "content": content[:1000],
        "summary": summary[:300],
        "importance": importance,
        "confidence": confidence,
    }


def _clamp_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def _get_message(session: Session, conversation_id: int, message_id: int) -> ChatMessage:
    message = session.get(ChatMessage, message_id)
    if message is None or message.conversation_id != conversation_id:
        raise ValueError(f"ChatMessage {message_id} not found in conversation {conversation_id}.")
    return message


def _to_message_payload(message: ChatMessage) -> ChatMessagePayload:
    return {
        "id": message.id or 0,
        "role": message.role,
        "content": message.content,
        "token_count": message.token_count,
    }


def _resolve_int(state: ConversationMemoryGraphState, key: str) -> int:
    value = state.get(key)
    if value is None:
        raise ValueError(f"{key} is required.")
    return int(value)
