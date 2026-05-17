from sqlmodel import Session, col, select

from app.agent.graphs.conversation_summary.nodes import DEFAULT_SUMMARY_TRIGGER_TOKENS
from app.jobs.models import GraphName, JobType
from app.jobs.queue import ACTIVE_STATUSES, enqueue_job
from app.models.chat_message import ChatMessage
from app.models.conversation import Conversation
from app.models.job import Job
from app.rag.chunking.tokenizer import count_tokens


def enqueue_conversation_summary_job_if_needed(
    session: Session,
    conversation_id: int,
    *,
    trigger_tokens: int = DEFAULT_SUMMARY_TRIGGER_TOKENS,
) -> Job | None:
    """当未摘要消息超过阈值时，为会话创建滚动摘要 job。

    参数：
      session: 调用方管理事务的数据库 session。
      conversation_id: 需要检查的会话 ID。
      trigger_tokens: 未摘要消息 token 超过该值才入队。

    返回：
      Job: 创建或复用的活跃 job。
      None: 当前还不需要摘要。
    """

    conversation = session.get(Conversation, conversation_id)
    if conversation is None:
        return None
    if not conversation_needs_summary(session, conversation, trigger_tokens=trigger_tokens):
        return None

    dedupe_key = _summary_dedupe_key(conversation_id)
    existing = _find_active_summary_job(session, dedupe_key)
    if existing:
        return existing
    return enqueue_job(
        session,
        job_type=JobType.CONVERSATION_SUMMARY.value,
        graph_name=GraphName.CONVERSATION_SUMMARY.value,
        payload={"conversation_id": conversation_id},
        dedupe_key=dedupe_key,
    )


def conversation_needs_summary(
    session: Session,
    conversation: Conversation,
    *,
    trigger_tokens: int = DEFAULT_SUMMARY_TRIGGER_TOKENS,
) -> bool:
    """判断 conversation 是否已经积累了足够多的未摘要消息。"""

    return unsummarized_token_count(session, conversation) > trigger_tokens


def unsummarized_token_count(session: Session, conversation: Conversation) -> int:
    """计算 summary_message_id 之后的消息 token 总量。"""

    statement = (
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation.id)
        .where(ChatMessage.status == "completed")
        .where(col(ChatMessage.role).in_(["user", "assistant"]))
    )
    if conversation.summary_message_id is not None:
        statement = statement.where(ChatMessage.id > conversation.summary_message_id)
    messages = session.exec(statement).all()
    return sum(
        message.token_count if message.token_count > 0 else count_tokens(message.content)
        for message in messages
    )


def _find_active_summary_job(session: Session, dedupe_key: str) -> Job | None:
    return session.exec(
        select(Job).where(
            Job.dedupe_key == dedupe_key,
            col(Job.status).in_(ACTIVE_STATUSES),
        )
    ).first()


def _summary_dedupe_key(conversation_id: int) -> str:
    return f"{JobType.CONVERSATION_SUMMARY.value}:conversation:{conversation_id}"
