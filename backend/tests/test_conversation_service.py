import pytest
from fastapi import HTTPException

from app.models.chat_turn import ChatTurn
from app.schemas.conversation import ChatMessageCreate, ConversationCreate
from app.services.conversation_service import (
    append_message,
    create_conversation,
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
