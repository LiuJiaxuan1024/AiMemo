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
from app.services.memory_consolidation_service import (
    ConsolidationJudge,
    NormalizedMemoryCandidate,
    consolidate_memory_candidates,
)
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
    """执行归并决策，把长期记忆写入或更新到 longtermmemory。

    复杂的重复判断已经由 consolidate_memories 完成。本节点只负责执行
    skip/create/update，并在 create/update 前再次做幂等保护。
    """

    def write_memories(
        state: ConversationMemoryGraphState,
    ) -> ConversationMemoryGraphState:
        result = state.get("consolidation_result") or {}
        decisions = result.get("decisions", [])
        if not isinstance(decisions, list):
            raise ValueError("consolidation_result.decisions must be a list.")

        source_id = _resolve_int(state, "assistant_message_id")
        written_ids: list[int] = []
        with session_factory() as session:
            for raw_decision in decisions:
                decision = _normalize_decision(raw_decision)
                if decision is None or decision["action"] == "skip":
                    continue

                if decision["action"] == "update":
                    memory = _apply_update_decision(session, decision)
                else:
                    memory = _apply_create_decision(session, decision, source_id)

                if memory is not None and memory.id is not None:
                    written_ids.append(memory.id)
            session.commit()
        return {"written_memory_ids": written_ids}

    return write_memories


def build_consolidate_memories_node(
    session_factory: SessionFactory,
    judge: ConsolidationJudge | None = None,
):
    """归并长期记忆候选，避免语义重复内容写入 L4。

    该节点结果会进入 checkpoint。若归并后进程中断，恢复时 write_memories
    可以直接执行 decisions，不重复调用 LLM judge。
    """

    def consolidate_memories(
        state: ConversationMemoryGraphState,
    ) -> ConversationMemoryGraphState:
        result = state.get("extraction_result") or {}
        memories = result.get("memories", [])
        if not isinstance(memories, list):
            raise ValueError("extraction_result.memories must be a list.")

        candidates = [
            NormalizedMemoryCandidate(**normalized)
            for raw_memory in memories
            if (normalized := _normalize_memory(raw_memory)) is not None
        ]
        with session_factory() as session:
            decisions = consolidate_memory_candidates(session, candidates, judge=judge)
        return {"consolidation_result": {"decisions": decisions}}

    return consolidate_memories


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
        "\"memory_key\":\"稳定槽位键，可为空，例如 user.preferred_name\","
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
        "- 能归入稳定槽位时填写 memory_key，例如用户希望被如何称呼用 user.preferred_name。\n"
        "- 同一个 memory_key 代表同一条可更新记忆，即使 category 表达不同也应归并。\n"
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
    memory_key = _normalize_memory_key(str(raw_memory.get("memory_key") or ""))
    summary = str(raw_memory.get("summary") or content).strip()
    return {
        "category": category,
        "memory_key": memory_key,
        "content": content[:1000],
        "summary": summary[:300],
        "importance": importance,
        "confidence": confidence,
    }


def _normalize_decision(raw_decision: Any) -> dict[str, Any] | None:
    if not isinstance(raw_decision, dict):
        return None
    action = str(raw_decision.get("action") or "create").strip().lower()
    if action not in {"skip", "create", "update"}:
        action = "create"

    content = str(raw_decision.get("content") or "").strip()
    if not content:
        return None
    category = str(raw_decision.get("category") or "fact").strip().lower()
    if category not in {"preference", "identity", "goal", "instruction", "event", "fact"}:
        category = "fact"
    memory_key = _normalize_memory_key(str(raw_decision.get("memory_key") or ""))
    existing_memory_id = raw_decision.get("existing_memory_id")
    try:
        existing_memory_id = int(existing_memory_id) if existing_memory_id is not None else None
    except (TypeError, ValueError):
        existing_memory_id = None
    return {
        "action": action,
        "existing_memory_id": existing_memory_id,
        "category": category,
        "memory_key": memory_key,
        "content": content[:1000],
        "summary": str(raw_decision.get("summary") or content).strip()[:300],
        "importance": _clamp_float(raw_decision.get("importance", 0.0)),
        "confidence": _clamp_float(raw_decision.get("confidence", 0.0)),
    }


def _apply_create_decision(
    session: Session,
    decision: dict[str, Any],
    source_id: int,
) -> LongTermMemory | None:
    memory_hash = build_memory_content_hash(decision["category"], decision["content"])
    existing = session.exec(
        select(LongTermMemory).where(LongTermMemory.content_hash == memory_hash)
    ).first()
    if existing:
        return None

    memory = LongTermMemory(
        level=4,
        category=decision["category"],
        memory_key=decision["memory_key"],
        content=decision["content"],
        summary=decision["summary"],
        importance=decision["importance"],
        confidence=decision["confidence"],
        source_type="chat_message",
        source_id=source_id,
        status="active",
        content_hash=memory_hash,
        updated_at=utc_now(),
    )
    session.add(memory)
    session.flush()
    return memory


def _apply_update_decision(
    session: Session,
    decision: dict[str, Any],
) -> LongTermMemory | None:
    memory_id = decision.get("existing_memory_id")
    if memory_id is None:
        return None

    memory = session.get(LongTermMemory, memory_id)
    if memory is None or memory.status != "active":
        return None

    memory.category = decision["category"]
    if decision["memory_key"]:
        memory.memory_key = decision["memory_key"]
    memory.content = decision["content"]
    memory.summary = decision["summary"]
    memory.importance = max(memory.importance, decision["importance"])
    memory.confidence = max(memory.confidence, decision["confidence"])
    memory.content_hash = build_memory_content_hash(memory.category, memory.content)
    memory.updated_at = utc_now()
    session.add(memory)
    session.flush()
    return memory


def _clamp_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))


def _normalize_memory_key(memory_key: str) -> str:
    """归一化模型输出的长期记忆槽位键。

    槽位键是给系统用的稳定索引，不展示给模型之外的普通用户。这里允许为空；
    非空时限制为简单 ASCII，避免模型输出中文或空格导致后续查询不稳定。
    """

    normalized = memory_key.strip().lower()
    if not normalized:
        return ""
    allowed = set("abcdefghijklmnopqrstuvwxyz0123456789._:-")
    if any(character not in allowed for character in normalized):
        return ""
    return normalized[:120]


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
