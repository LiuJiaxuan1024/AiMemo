from sqlmodel import select

from app.jobs.models import GraphName, JobType
from app.jobs.reconciler import reconcile_missing_jobs
from app.models.chat_message import ChatMessage
from app.models.job import Job
from app.schemas.conversation import ConversationCreate
from app.services.conversation_service import create_conversation
from app.services.long_term_memory_service import enqueue_conversation_memory_job_if_needed


def test_enqueue_conversation_memory_job_once_per_assistant_message(session):
    conversation = create_conversation(session, ConversationCreate(title="记忆任务"))
    user = _add_message(session, conversation.id, "user", "我喜欢黑咖啡。")
    assistant = _add_message(session, conversation.id, "assistant", "记下了。", user.id)

    first = enqueue_conversation_memory_job_if_needed(
        session,
        conversation_id=conversation.id,
        user_message_id=user.id,
        assistant_message_id=assistant.id,
    )
    second = enqueue_conversation_memory_job_if_needed(
        session,
        conversation_id=conversation.id,
        user_message_id=user.id,
        assistant_message_id=assistant.id,
    )
    session.commit()

    jobs = session.exec(select(Job)).all()
    assert first is not None
    assert second is None
    assert len(jobs) == 1
    assert jobs[0].type == JobType.CONVERSATION_MEMORY.value
    assert jobs[0].graph_name == GraphName.CONVERSATION_MEMORY.value


def test_reconcile_enqueues_missing_conversation_memory_job(session):
    conversation = create_conversation(session, ConversationCreate(title="记忆补建"))
    user = _add_message(session, conversation.id, "user", "我的目标是学会 LangGraph。")
    _add_message(session, conversation.id, "assistant", "这个目标值得跟进。", user.id)

    result = reconcile_missing_jobs(session)

    jobs = session.exec(select(Job)).all()
    assert result.memory_jobs_created == 1
    assert jobs[0].type == JobType.CONVERSATION_MEMORY.value


def _add_message(
    session,
    conversation_id: int,
    role: str,
    content: str,
    parent_id: int | None = None,
) -> ChatMessage:
    message = ChatMessage(
        conversation_id=conversation_id,
        role=role,
        content=content,
        parent_id=parent_id,
    )
    session.add(message)
    session.commit()
    session.refresh(message)
    return message
