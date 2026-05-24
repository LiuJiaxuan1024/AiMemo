from sqlmodel import Session, col, select

from app.agent.graphs.conversation_title.nodes import DEFAULT_TITLE
from app.jobs.models import GraphName, JobType
from app.jobs.queue import ACTIVE_STATUSES, enqueue_job
from app.models.chat_message import ChatMessage
from app.models.conversation import Conversation
from app.models.job import Job


def enqueue_conversation_title_job_if_needed(
    session: Session,
    conversation_id: int,
) -> Job | None:
    """会话还在默认标题且已有第一条 user 消息时，入队 title 生成 job。"""

    conversation = session.get(Conversation, conversation_id)
    if conversation is None:
        return None
    if conversation.title and conversation.title != DEFAULT_TITLE:
        return None

    has_user_msg = session.exec(
        select(ChatMessage.id)
        .where(ChatMessage.conversation_id == conversation_id)
        .where(ChatMessage.role == "user")
        .where(ChatMessage.status == "completed")
        .limit(1)
    ).first()
    if not has_user_msg:
        return None

    dedupe_key = _title_dedupe_key(conversation_id)
    existing = _find_active_title_job(session, dedupe_key)
    if existing:
        return existing
    return enqueue_job(
        session,
        job_type=JobType.CONVERSATION_TITLE.value,
        graph_name=GraphName.CONVERSATION_TITLE.value,
        payload={"conversation_id": conversation_id},
        dedupe_key=dedupe_key,
    )


def _title_dedupe_key(conversation_id: int) -> str:
    return f"conversation_title:conversation:{conversation_id}"


def _find_active_title_job(session: Session, dedupe_key: str) -> Job | None:
    return session.exec(
        select(Job)
        .where(Job.dedupe_key == dedupe_key)
        .where(col(Job.status).in_(ACTIVE_STATUSES))
        .order_by(Job.id.desc())
        .limit(1)
    ).first()
