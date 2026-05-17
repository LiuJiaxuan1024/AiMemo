from pathlib import Path

from app.agent.graphs.conversation_summary.graph import run_conversation_summary_graph
from app.jobs.models import GraphName, JobType
from app.jobs.queue import enqueue_job
from app.models.chat_message import ChatMessage
from app.models.conversation import Conversation
from app.schemas.conversation import ConversationCreate
from app.services.conversation_service import create_conversation


def test_conversation_summary_graph_writes_rolling_summary(
    session,
    session_factory,
    tmp_path: Path,
):
    conversation = create_conversation(session, ConversationCreate(title="摘要测试"))
    _add_message(session, conversation.id, "user", "我最近在准备开源项目。", 900)
    assistant = _add_message(session, conversation.id, "assistant", "我们可以先做架构。", 900)
    job = enqueue_job(
        session,
        job_type=JobType.CONVERSATION_SUMMARY.value,
        graph_name=GraphName.CONVERSATION_SUMMARY.value,
        payload={"conversation_id": conversation.id},
        dedupe_key=f"{JobType.CONVERSATION_SUMMARY.value}:conversation:{conversation.id}",
    )
    session.commit()
    session.refresh(job)

    def fake_summary(old_summary, messages):
        assert old_summary == ""
        assert [message["role"] for message in messages] == ["user", "assistant"]
        return "用户正在准备一个开源项目，当前重点是先完成架构设计。"

    run_conversation_summary_graph(
        job,
        session_factory=session_factory,
        checkpoint_path=str(tmp_path / "checkpoints.db"),
        summary_generator=fake_summary,
    )

    session.expire_all()
    updated = session.get(Conversation, conversation.id)
    assert updated is not None
    assert updated.summary == "用户正在准备一个开源项目，当前重点是先完成架构设计。"
    assert updated.summary_message_id == assistant.id


def test_conversation_summary_graph_resumes_after_generate_without_recalling_llm(
    session,
    session_factory,
    tmp_path: Path,
):
    conversation = create_conversation(session, ConversationCreate(title="摘要恢复"))
    _add_message(session, conversation.id, "user", "这是一段需要摘要的长对话。", 1800)
    job = enqueue_job(
        session,
        job_type=JobType.CONVERSATION_SUMMARY.value,
        graph_name=GraphName.CONVERSATION_SUMMARY.value,
        payload={"conversation_id": conversation.id},
    )
    session.commit()
    session.refresh(job)
    checkpoint_path = tmp_path / "checkpoints.db"
    calls: list[int] = []

    def fake_summary(old_summary, messages):
        calls.append(len(messages))
        return "这是一份已经生成过的摘要。"

    run_conversation_summary_graph(
        job,
        session_factory=session_factory,
        checkpoint_path=str(checkpoint_path),
        summary_generator=fake_summary,
        interrupt_after=["summarize_messages"],
    )

    session.expire_all()
    conversation_after_interrupt = session.get(Conversation, conversation.id)
    assert conversation_after_interrupt is not None
    assert conversation_after_interrupt.summary == ""

    run_conversation_summary_graph(
        job,
        session_factory=session_factory,
        checkpoint_path=str(checkpoint_path),
        summary_generator=fake_summary,
    )

    session.expire_all()
    updated = session.get(Conversation, conversation.id)
    assert calls == [1]
    assert updated is not None
    assert updated.summary == "这是一份已经生成过的摘要。"


def test_conversation_summary_graph_skips_below_threshold(
    session,
    session_factory,
    tmp_path: Path,
):
    conversation = create_conversation(session, ConversationCreate(title="未达阈值"))
    _add_message(session, conversation.id, "user", "短消息", 10)
    job = enqueue_job(
        session,
        job_type=JobType.CONVERSATION_SUMMARY.value,
        graph_name=GraphName.CONVERSATION_SUMMARY.value,
        payload={"conversation_id": conversation.id},
    )
    session.commit()
    session.refresh(job)
    calls: list[str] = []

    def fake_summary(old_summary, messages):
        calls.append("called")
        return "不应该生成"

    run_conversation_summary_graph(
        job,
        session_factory=session_factory,
        checkpoint_path=str(tmp_path / "checkpoints.db"),
        summary_generator=fake_summary,
    )

    session.expire_all()
    updated = session.get(Conversation, conversation.id)
    assert calls == []
    assert updated is not None
    assert updated.summary == ""


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
