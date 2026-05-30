import pytest
from fastapi import HTTPException
from sqlmodel import select

from app.jobs.models import GraphName, JobType
from app.jobs.queue import enqueue_job
from app.models.agent_operation import AgentOperation
from app.models.background_task import BackgroundTask
from app.models.chat_message import ChatMessage
from app.models.chat_turn import ChatTurn
from app.models.conversation import Conversation
from app.models.job import Job
from app.models.long_term_memory import LongTermMemory
from app.schemas.conversation import ChatMessageCreate, ConversationCreate
from app.services.conversation_service import (
    append_message,
    create_conversation,
    delete_conversation,
    delete_message_branch,
    get_conversation,
    list_messages,
)


def test_create_conversation_assigns_langgraph_thread_id(session):
    conversation = create_conversation(session, ConversationCreate(title="记忆问答"))

    assert conversation.title == "记忆问答"
    assert conversation.status == "active"
    assert conversation.langgraph_thread_id == f"conversation:{conversation.id}"


def test_append_message_links_to_latest_message_by_default(session):
    conversation = create_conversation(session, ConversationCreate())

    first = append_message(
        session,
        conversation.id,
        ChatMessageCreate(role="user", content="你好"),
    )
    second = append_message(
        session,
        conversation.id,
        ChatMessageCreate(role="assistant", content="你好，我在。"),
    )

    messages = list_messages(session, conversation.id)
    assert first.parent_id is None
    assert second.parent_id == first.id
    assert [message.id for message in messages] == [first.id, second.id]
    assert messages[0].token_count > 0


def test_append_message_rejects_parent_from_other_conversation(session):
    first_conversation = create_conversation(session, ConversationCreate(title="A"))
    second_conversation = create_conversation(session, ConversationCreate(title="B"))
    parent = append_message(
        session,
        first_conversation.id,
        ChatMessageCreate(role="user", content="第一条消息"),
    )

    with pytest.raises(HTTPException) as exc_info:
        append_message(
            session,
            second_conversation.id,
            ChatMessageCreate(role="user", content="错误分支", parent_id=parent.id),
        )

    assert exc_info.value.status_code == 400


def test_list_messages_includes_turn_id_for_graph_backed_assistant_message(session):
    conversation = create_conversation(session, ConversationCreate(title="Graph 测试"))
    user = append_message(
        session,
        conversation.id,
        ChatMessageCreate(role="user", content="你好"),
    )
    assistant = append_message(
        session,
        conversation.id,
        ChatMessageCreate(role="assistant", content="你好，我在。"),
    )
    turn = ChatTurn(
        conversation_id=conversation.id,
        thread_id=f"conversation:{conversation.id}",
        user_message_id=user.id,
        assistant_message_id=assistant.id,
    )
    session.add(turn)
    session.commit()
    session.refresh(turn)

    messages = list_messages(session, conversation.id)

    assert messages[0].turn_id is None
    assert messages[1].turn_id == turn.id


def test_get_conversation_raises_404_for_missing_id(session):
    with pytest.raises(HTTPException) as exc_info:
        get_conversation(session, 404)

    assert exc_info.value.status_code == 404


def test_delete_conversation_cascades_related_resources(session, monkeypatch):
    target = create_conversation(session, ConversationCreate(title="待删除"))
    keeper = create_conversation(session, ConversationCreate(title="保留"))

    user = append_message(
        session,
        target.id,
        ChatMessageCreate(role="user", content="问题"),
    )
    assistant = append_message(
        session,
        target.id,
        ChatMessageCreate(role="assistant", content="回答"),
    )
    keeper_msg = append_message(
        session,
        keeper.id,
        ChatMessageCreate(role="user", content="另一段对话的问题"),
    )

    turn = ChatTurn(
        conversation_id=target.id,
        thread_id=f"conversation:{target.id}",
        user_message_id=user.id,
        assistant_message_id=assistant.id,
    )
    session.add(turn)

    target_memory = LongTermMemory(
        level=4,
        category="fact",
        content="待删除的长期记忆",
        source_type="chat_message",
        source_id=user.id,
        content_hash="hash-target",
    )
    keeper_memory = LongTermMemory(
        level=4,
        category="fact",
        content="保留对话的记忆",
        source_type="chat_message",
        source_id=keeper_msg.id,
        content_hash="hash-keeper",
    )
    session.add(target_memory)
    session.add(keeper_memory)

    target_op = AgentOperation(
        conversation_id=target.id,
        operation_type="read",
        tool_name="read_note",
    )
    keeper_op = AgentOperation(
        conversation_id=keeper.id,
        operation_type="read",
        tool_name="read_note",
    )
    session.add(target_op)
    session.add(keeper_op)

    target_bg = BackgroundTask(
        task_id=f"bg-target-{target.id}",
        conversation_id=target.id,
        command="echo hi",
        cwd=".",
        status="exited",
    )
    keeper_bg = BackgroundTask(
        task_id=f"bg-keeper-{keeper.id}",
        conversation_id=keeper.id,
        command="echo hi",
        cwd=".",
        status="exited",
    )
    session.add(target_bg)
    session.add(keeper_bg)

    session.commit()

    enqueue_job(
        session,
        job_type=JobType.CONVERSATION_TITLE.value,
        graph_name=GraphName.CONVERSATION_TITLE.value,
        payload={"conversation_id": target.id},
        dedupe_key=f"conversation_title:conversation:{target.id}",
    )
    enqueue_job(
        session,
        job_type=JobType.CONVERSATION_SUMMARY.value,
        graph_name=GraphName.CONVERSATION_SUMMARY.value,
        payload={"conversation_id": target.id},
        dedupe_key=f"conversation_summary:conversation:{target.id}",
    )
    enqueue_job(
        session,
        job_type=JobType.CONVERSATION_TITLE.value,
        graph_name=GraphName.CONVERSATION_TITLE.value,
        payload={"conversation_id": keeper.id},
        dedupe_key=f"conversation_title:conversation:{keeper.id}",
    )
    session.commit()

    kill_calls: list[str] = []
    prune_calls: list[str] = []

    class FakePool:
        def kill(self, task_id, *, reason=""):
            kill_calls.append(task_id)

        def prune(self, task_id):
            prune_calls.append(task_id)

    import app.local_operator.background_command as bg_module
    monkeypatch.setattr(bg_module, "pool", FakePool())

    delete_conversation(session, target.id)
    session.expire_all()

    assert session.get(Conversation, target.id) is None
    assert session.get(Conversation, keeper.id) is not None

    remaining_messages = session.exec(
        select(ChatMessage).where(ChatMessage.conversation_id == target.id)
    ).all()
    assert remaining_messages == []
    assert (
        session.exec(
            select(ChatMessage).where(ChatMessage.conversation_id == keeper.id)
        ).first()
        is not None
    )

    remaining_turns = session.exec(
        select(ChatTurn).where(ChatTurn.conversation_id == target.id)
    ).all()
    assert remaining_turns == []

    remaining_memories = session.exec(
        select(LongTermMemory).where(LongTermMemory.source_id == user.id)
    ).all()
    assert remaining_memories == []
    assert (
        session.exec(
            select(LongTermMemory).where(LongTermMemory.source_id == keeper_msg.id)
        ).first()
        is not None
    )

    remaining_ops = session.exec(
        select(AgentOperation).where(AgentOperation.conversation_id == target.id)
    ).all()
    assert remaining_ops == []
    assert (
        session.exec(
            select(AgentOperation).where(AgentOperation.conversation_id == keeper.id)
        ).first()
        is not None
    )

    remaining_jobs = session.exec(
        select(Job).where(Job.dedupe_key.like(f"conversation_%:conversation:{target.id}"))
    ).all()
    assert remaining_jobs == []
    keeper_jobs = session.exec(
        select(Job).where(Job.dedupe_key == f"conversation_title:conversation:{keeper.id}")
    ).all()
    assert len(keeper_jobs) == 1

    assert kill_calls == [target_bg.task_id]
    assert prune_calls == [target_bg.task_id]

    remaining_bg = session.exec(
        select(BackgroundTask).where(BackgroundTask.conversation_id == target.id)
    ).all()
    assert remaining_bg == []
    assert (
        session.exec(
            select(BackgroundTask).where(BackgroundTask.conversation_id == keeper.id)
        ).first()
        is not None
    )


def test_delete_conversation_raises_404_for_missing_id(session):
    with pytest.raises(HTTPException) as exc_info:
        delete_conversation(session, 404)
    assert exc_info.value.status_code == 404


def test_delete_message_branch_removes_turn_descendants_and_memories(session):
    conversation = create_conversation(session, ConversationCreate(title="分支删除"))
    user1 = append_message(session, conversation.id, ChatMessageCreate(role="user", content="问题1"))
    assistant1 = append_message(
        session,
        conversation.id,
        ChatMessageCreate(role="assistant", content="回答1"),
    )
    user2 = append_message(session, conversation.id, ChatMessageCreate(role="user", content="问题2"))
    assistant2 = append_message(
        session,
        conversation.id,
        ChatMessageCreate(role="assistant", content="回答2"),
    )
    turn1 = ChatTurn(
        conversation_id=conversation.id,
        thread_id=f"conversation:{conversation.id}",
        user_message_id=user1.id,
        assistant_message_id=assistant1.id,
        status="completed",
    )
    turn2 = ChatTurn(
        conversation_id=conversation.id,
        thread_id=f"conversation:{conversation.id}",
        user_message_id=user2.id,
        assistant_message_id=assistant2.id,
        status="completed",
    )
    memory = LongTermMemory(
        level=4,
        category="fact",
        content="来自第二轮的记忆",
        source_type="chat_message",
        source_id=assistant2.id,
        content_hash="branch-delete-memory",
    )
    session.add(turn1)
    session.add(turn2)
    session.add(memory)
    session.commit()

    delete_message_branch(session, conversation.id, assistant1.id)

    assert list_messages(session, conversation.id) == []
    assert session.exec(select(ChatTurn).where(ChatTurn.conversation_id == conversation.id)).all() == []
    assert session.exec(select(LongTermMemory).where(LongTermMemory.source_id == assistant2.id)).all() == []


def test_delete_message_branch_rejects_running_turn(session):
    conversation = create_conversation(session, ConversationCreate(title="运行中"))
    user = append_message(session, conversation.id, ChatMessageCreate(role="user", content="问题"))
    assistant = append_message(
        session,
        conversation.id,
        ChatMessageCreate(role="assistant", content="回答", parent_id=user.id, status="streaming"),
    )
    session.add(
        ChatTurn(
            conversation_id=conversation.id,
            thread_id=f"conversation:{conversation.id}",
            user_message_id=user.id,
            assistant_message_id=assistant.id,
            status="running",
        )
    )
    session.commit()

    with pytest.raises(HTTPException) as exc_info:
        delete_message_branch(session, conversation.id, assistant.id)

    assert exc_info.value.status_code == 409
