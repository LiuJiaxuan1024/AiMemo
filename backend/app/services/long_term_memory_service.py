from sqlmodel import Session, desc, select

from app.jobs.models import GraphName, JobType
from app.jobs.queue import enqueue_job
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
            .order_by(desc(LongTermMemory.importance), desc(LongTermMemory.updated_at))
            .limit(limit)
        ).all()
    )


def _memory_dedupe_key(assistant_message_id: int) -> str:
    return f"{JobType.CONVERSATION_MEMORY.value}:assistant_message:{assistant_message_id}"
