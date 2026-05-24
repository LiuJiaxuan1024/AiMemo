from pathlib import Path

from app.agent.graphs.conversation_title.graph import run_conversation_title_graph
from app.jobs.models import GraphName, JobType
from app.jobs.queue import enqueue_job
from app.models.chat_message import ChatMessage
from app.models.conversation import Conversation
from app.schemas.conversation import ConversationCreate
from app.services.conversation_service import create_conversation
from app.services.conversation_title_service import (
    enqueue_conversation_title_job_if_needed,
)


def test_conversation_title_graph_writes_title(
    session,
    session_factory,
    tmp_path: Path,
):
    conversation = create_conversation(session, ConversationCreate(title=""))
    _add_user_message(session, conversation.id, "请帮我对比一下 Polars 和 Pandas 的差异")
    job = enqueue_job(
        session,
        job_type=JobType.CONVERSATION_TITLE.value,
        graph_name=GraphName.CONVERSATION_TITLE.value,
        payload={"conversation_id": conversation.id},
        dedupe_key=f"conversation_title:conversation:{conversation.id}",
    )
    session.commit()
    session.refresh(job)

    captured: list[str] = []

    def fake_title(message: str) -> str:
        captured.append(message)
        return "Polars 与 Pandas 对比"

    run_conversation_title_graph(
        job,
        session_factory=session_factory,
        checkpoint_path=str(tmp_path / "checkpoints.db"),
        title_generator=fake_title,
    )

    session.expire_all()
    updated = session.get(Conversation, conversation.id)
    assert updated is not None
    assert updated.title == "Polars 与 Pandas 对比"
    assert captured == ["请帮我对比一下 Polars 和 Pandas 的差异"]


def test_conversation_title_graph_skips_when_title_already_set(
    session,
    session_factory,
    tmp_path: Path,
):
    conversation = create_conversation(session, ConversationCreate(title="人工设定标题"))
    _add_user_message(session, conversation.id, "随便问一句")
    job = enqueue_job(
        session,
        job_type=JobType.CONVERSATION_TITLE.value,
        graph_name=GraphName.CONVERSATION_TITLE.value,
        payload={"conversation_id": conversation.id},
    )
    session.commit()
    session.refresh(job)

    calls: list[str] = []

    def fake_title(message: str) -> str:
        calls.append(message)
        return "不应被写入"

    run_conversation_title_graph(
        job,
        session_factory=session_factory,
        checkpoint_path=str(tmp_path / "checkpoints.db"),
        title_generator=fake_title,
    )

    session.expire_all()
    updated = session.get(Conversation, conversation.id)
    assert updated is not None
    assert updated.title == "人工设定标题"
    assert calls == []


def test_enqueue_conversation_title_job_is_idempotent(session):
    conversation = create_conversation(session, ConversationCreate(title=""))
    _add_user_message(session, conversation.id, "今天天气真好")

    first = enqueue_conversation_title_job_if_needed(session, conversation.id)
    second = enqueue_conversation_title_job_if_needed(session, conversation.id)
    assert first is not None
    assert second is not None
    assert first.id == second.id


def test_enqueue_conversation_title_job_skips_when_no_user_message(session):
    conversation = create_conversation(session, ConversationCreate(title=""))
    job = enqueue_conversation_title_job_if_needed(session, conversation.id)
    assert job is None


def _add_user_message(session, conversation_id: int, content: str) -> ChatMessage:
    message = ChatMessage(
        conversation_id=conversation_id,
        role="user",
        content=content,
        status="completed",
        token_count=len(content),
    )
    session.add(message)
    session.commit()
    session.refresh(message)
    return message
