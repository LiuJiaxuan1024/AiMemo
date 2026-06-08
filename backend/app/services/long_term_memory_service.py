import json
from datetime import datetime

from sqlmodel import Session, desc, select

from app.jobs.models import GraphName, JobType
from app.jobs.queue import enqueue_job
from app.models.chat_message import ChatMessage
from app.models.conversation import Conversation
from app.models.job import Job
from app.models.long_term_memory import LongTermMemory


def enqueue_conversation_memory_job_if_needed(
    session: Session,
    *,
    conversation_id: int,
    user_message_id: int,
    assistant_message_id: int,
) -> Job | None:
    """为一轮对话创建长期记忆抽取 job。

    同一个 assistant_message_id 只会创建一次任务。即使任务已经 completed，
    也不会再次创建，避免“没有抽出记忆”的历史消息被反复扫描。
    """

    dedupe_key = _memory_dedupe_key(assistant_message_id)
    existing = session.exec(select(Job).where(Job.dedupe_key == dedupe_key)).first()
    if existing:
        return None
    return enqueue_job(
        session,
        job_type=JobType.CONVERSATION_MEMORY.value,
        graph_name=GraphName.CONVERSATION_MEMORY.value,
        payload={
            "conversation_id": conversation_id,
            "user_message_id": user_message_id,
            "assistant_message_id": assistant_message_id,
        },
        dedupe_key=dedupe_key,
    )


def list_core_memories(
    session: Session,
    *,
    limit: int = 8,
) -> list[LongTermMemory]:
    """读取 L4 prompt 使用的核心长期记忆。"""

    return list(
        session.exec(
            select(LongTermMemory)
            .where(LongTermMemory.status == "active")
            .where(LongTermMemory.level == 4)
            .order_by(
                desc(LongTermMemory.importance),
                desc(LongTermMemory.reinforcement_count),
                desc(LongTermMemory.updated_at),
            )
            .limit(limit)
        ).all()
    )


def format_core_memory_for_prompt(memory: LongTermMemory) -> str:
    """把长期记忆渲染成 L4 prompt 中的单行条目。"""

    key = f", key={memory.memory_key}" if memory.memory_key else ""
    reinforced = (
        f", reinforced={memory.reinforcement_count}"
        if memory.reinforcement_count > 1
        else ""
    )
    evidence = f", evidence={memory.evidence_count}" if memory.evidence_count > 1 else ""
    return (
        f"[{memory.category}{key}, importance={memory.importance:.2f}, "
        f"confidence={memory.confidence:.2f}{reinforced}{evidence}] {memory.content}"
    )


def format_core_memory_with_sources_for_prompt(
    session: Session,
    memory: LongTermMemory,
    *,
    include_sources: bool | None = None,
    source_limit: int = 2,
) -> str:
    """把长期记忆渲染为 prompt 条目，并按需附带压缩来源线索。

    来源线索用于帮助精灵理解记忆的语境、可信度和适用边界；它不是审计日志，
    所以只取少量证据并做短摘录，避免 L4 prompt 被历史原文淹没。
    """

    base = format_core_memory_for_prompt(memory)
    should_include_sources = _should_include_source_traces(memory) if include_sources is None else include_sources
    if not should_include_sources:
        return base

    traces = resolve_memory_source_traces(session, memory, limit=source_limit)
    if not traces:
        return base

    trace_lines = "\n".join(f"  - {trace}" for trace in traces)
    return f"{base}\n  来源线索:\n{trace_lines}"


def resolve_memory_source_traces(
    session: Session,
    memory: LongTermMemory,
    *,
    limit: int = 2,
) -> list[str]:
    """读取长期记忆的来源消息，并压缩成可放进 prompt 的短线索。"""

    if memory.source_type != "chat_message":
        return []

    traces: list[str] = []
    seen: set[int] = set()
    for source_id in _memory_source_ids(memory):
        if source_id in seen:
            continue
        seen.add(source_id)
        message = session.get(ChatMessage, source_id)
        if message is None:
            continue
        traces.append(_format_source_trace(session, message))
        if len(traces) >= max(1, limit):
            break
    return traces


def _memory_dedupe_key(assistant_message_id: int) -> str:
    return f"{JobType.CONVERSATION_MEMORY.value}:assistant_message:{assistant_message_id}"


def _should_include_source_traces(memory: LongTermMemory) -> bool:
    if memory.evidence_count > 1 or memory.reinforcement_count > 1:
        return True
    if memory.importance >= 0.85 or memory.confidence < 0.75:
        return True
    return memory.category in {"identity", "preference", "instruction", "goal"}


def _memory_source_ids(memory: LongTermMemory) -> list[int]:
    source_ids = _parse_int_list(memory.evidence_source_ids)
    if memory.source_id is not None and memory.source_id not in source_ids:
        source_ids.insert(0, memory.source_id)
    return source_ids


def _format_source_trace(session: Session, message: ChatMessage) -> str:
    conversation = session.get(Conversation, message.conversation_id)
    title = conversation.title if conversation else f"conversation #{message.conversation_id}"
    timestamp = _format_trace_time(message.created_at)
    if message.role == "assistant" and message.parent_id is not None:
        parent = session.get(ChatMessage, message.parent_id)
        if parent is not None and parent.role == "user":
            return (
                f"{timestamp}《{title}》用户说：{_compact_text(parent.content, 72)}；"
                f"助手回应：{_compact_text(message.content, 72)}"
            )
    return f"{timestamp}《{title}》{message.role}：{_compact_text(message.content, 130)}"


def _format_trace_time(value: datetime) -> str:
    return value.strftime("%Y-%m-%d")


def _compact_text(value: str, limit: int) -> str:
    text = " ".join((value or "").split())
    if len(text) <= limit:
        return text
    return f"{text[: max(0, limit - 1)]}…"


def _parse_int_list(value: str) -> list[int]:
    try:
        payload = json.loads(value or "[]")
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    result: list[int] = []
    for item in payload:
        try:
            result.append(int(item))
        except (TypeError, ValueError):
            continue
    return result
