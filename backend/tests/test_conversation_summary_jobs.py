from sqlmodel import select

from app.jobs.models import GraphName, JobType
from app.jobs.reconciler import reconcile_missing_jobs
from app.models.chat_message import ChatMessage
from app.models.job import Job
from app.schemas.conversation import ConversationCreate
from app.services.conversation_service import create_conversation
from app.services.conversation_summary_service import enqueue_conversation_summary_job_if_needed


def test_enqueue_conversation_summary_job_when_unsummarized_tokens_exceed_threshold(session):
    conversation = create_conversation(session, ConversationCreate(title="摘要入队"))
    _add_message(session, conversation.id, "user", "长消息", 1600)

    job = enqueue_conversation_summary_job_if_needed(session, conversation.id)
    session.commit()

    assert job is not None
    assert job.type == JobType.CONVERSATION_SUMMARY.value
    assert job.graph_name == GraphName.CONVERSATION_SUMMARY.value
    assert job.thread_id == f"job:{job.id}"


def test_enqueue_conversation_summary_job_dedupes_active_job(session):
    conversation = create_conversation(session, ConversationCreate(title="摘要去重"))
    _add_message(session, conversation.id, "user", "长消息", 1600)

    first = enqueue_conversation_summary_job_if_needed(session, conversation.id)
    second = enqueue_conversation_summary_job_if_needed(session, conversation.id)
    session.commit()

    jobs = session.exec(select(Job)).all()
    assert first is not None
    assert second is not None
    assert first.id == second.id
    assert len(jobs) == 1


def test_reconcile_enqueues_missing_conversation_summary_job(session):
    conversation = create_conversation(session, ConversationCreate(title="摘要补建"))
    _add_message(session, conversation.id, "user", "长消息", 1600)

    result = reconcile_missing_jobs(session)

    jobs = session.exec(select(Job)).all()
    assert result.summary_jobs_created == 1
    assert result.total_jobs_created == 1
    assert jobs[0].type == JobType.CONVERSATION_SUMMARY.value


def _add_message(
    session,
    conversation_id: int,
    role: str,
    content: str,
    token_count: int,
) -> ChatMessage:
    message = ChatMessage(
        conversation_id=conversation_id,
        role=role,
        content=content,
        token_count=token_count,
    )
    session.add(message)
    session.commit()
    session.refresh(message)
    return message
