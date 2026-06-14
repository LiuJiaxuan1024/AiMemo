import json
from collections.abc import Generator

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.database import get_session
from app.main import create_app
from app.models.chat_turn import ChatTurn
from app.schemas.conversation import ChatMessageCreate, ConversationCreate
from app.services.conversation_service import append_message, create_conversation


def _client(session: Session) -> TestClient:
    app = create_app()

    def override_get_session() -> Generator[Session, None, None]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    return TestClient(app)


def test_export_selected_conversation_messages_as_html(session: Session) -> None:
    conversation = create_conversation(session, ConversationCreate(title="导出测试"))
    first = append_message(session, conversation.id, ChatMessageCreate(role="user", content="第一条"))
    second = append_message(
        session,
        conversation.id,
        ChatMessageCreate(role="assistant", content="**回答**内容", parent_id=first.id),
    )
    third = append_message(
        session,
        conversation.id,
        ChatMessageCreate(role="user", content="不导出这一条", parent_id=second.id),
    )
    client = _client(session)

    response = client.post(
        f"/api/conversations/{conversation.id}/export",
        json={"message_ids": [first.id, second.id], "include_all": False},
    )

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "attachment;" in response.headers["content-disposition"]
    html = response.text
    assert "导出测试" in html
    assert "第一条" in html
    assert "<strong>回答</strong>内容" in html
    assert "data-open-followups" in html
    assert "aimemo-export-data" in html
    assert "不导出这一条" not in html
    assert f"message-{third.id}" not in html


def test_export_preserves_segment_followups_without_graph_payload(session: Session) -> None:
    conversation = create_conversation(session, ConversationCreate(title="追问导出"))
    user = append_message(session, conversation.id, ChatMessageCreate(role="user", content="解释一下。"))
    assistant = append_message(
        session,
        conversation.id,
        ChatMessageCreate(role="assistant", content="这里有一段可以追问的内容。", parent_id=user.id),
    )
    turn = ChatTurn(
        conversation_id=conversation.id,
        thread_id=conversation.langgraph_thread_id,
        user_message_id=user.id,
        assistant_message_id=assistant.id,
        status="completed",
        node_statuses=json.dumps({"plan_task": "succeeded", "agent": "succeeded"}, ensure_ascii=False),
        context_layers=json.dumps([{"name": "L1 recent", "used_tokens": 12}], ensure_ascii=False),
        retrieved_chunks=json.dumps([{"note_title": "测试笔记"}], ensure_ascii=False),
    )
    session.add(turn)
    session.commit()

    followup_payload = {
        "type": "segment_followup",
        "source_message_id": assistant.id,
        "segment_id": "seg-test",
        "original_text": "可以追问的内容",
        "user_question": "这段是什么意思？",
        "position": {"start": 4, "end": 11},
    }
    followup_user = append_message(
        session,
        conversation.id,
        ChatMessageCreate(role="user", content=json.dumps(followup_payload, ensure_ascii=False), parent_id=assistant.id),
    )
    followup_assistant = append_message(
        session,
        conversation.id,
        ChatMessageCreate(role="assistant", content="这段是在强调局部上下文。", parent_id=followup_user.id),
    )
    session.add(
        ChatTurn(
            conversation_id=conversation.id,
            thread_id=conversation.langgraph_thread_id,
            user_message_id=followup_user.id,
            assistant_message_id=followup_assistant.id,
            status="completed",
            node_statuses=json.dumps({"agent": "succeeded"}, ensure_ascii=False),
        )
    )
    session.commit()

    response = _client(session).post(
        f"/api/conversations/{conversation.id}/export",
        json={"message_ids": [assistant.id], "include_all": False, "include_graphs": True, "include_followups": True},
    )

    assert response.status_code == 200
    html = response.text
    assert "片段追问" in html
    assert "segment-followup-panel" in html
    assert "data-open-graph" not in html
    assert "可以追问的内容" in html
    assert "这段是什么意思？" in html
    assert "这段是在强调局部上下文。" in html
    assert '"graphs":{}' in html
    assert "plan_task" not in html
    assert "测试笔记" not in html
    assert "segment_followup" not in html

    snapshot_response = _client(session).post(
        f"/api/conversations/{conversation.id}/export/snapshot",
        json={"message_ids": [assistant.id], "include_all": False, "include_graphs": True, "include_followups": True},
    )

    assert snapshot_response.status_code == 200
    snapshot = snapshot_response.json()
    assert snapshot["schema_version"] == 1
    assert snapshot["conversation"]["title"] == "追问导出"
    assert len(snapshot["messages"]) == 1
    exported_message = snapshot["messages"][0]
    assert exported_message["id"] == assistant.id
    assert exported_message["content"] == "这里有一段可以追问的内容。"
    assert exported_message["content_html"]
    assert exported_message["graph_id"] is None
    assert len(exported_message["followup_threads"]) == 1
    exported_thread = exported_message["followup_threads"][0]
    assert exported_thread["segment_id"] == "seg-test"
    assert exported_thread["turns"][0]["question"] == "这段是什么意思？"
    assert exported_thread["turns"][0]["answer"] == "这段是在强调局部上下文。"
    assert exported_thread["turns"][0]["graph_id"] is None
    assert snapshot["graphs"] == {}


def test_export_multiple_conversation_snapshots(session: Session) -> None:
    first_conversation = create_conversation(session, ConversationCreate(title="第一段对话"))
    second_conversation = create_conversation(session, ConversationCreate(title="第二段对话"))
    first_user = append_message(
        session,
        first_conversation.id,
        ChatMessageCreate(role="user", content="第一段用户消息"),
    )
    append_message(
        session,
        first_conversation.id,
        ChatMessageCreate(role="assistant", content="第一段回答", parent_id=first_user.id),
    )
    second_user = append_message(
        session,
        second_conversation.id,
        ChatMessageCreate(role="user", content="第二段用户消息"),
    )
    second_assistant = append_message(
        session,
        second_conversation.id,
        ChatMessageCreate(role="assistant", content="第二段回答", parent_id=second_user.id),
    )
    session.add(
        ChatTurn(
            conversation_id=second_conversation.id,
            thread_id=second_conversation.langgraph_thread_id,
            user_message_id=second_user.id,
            assistant_message_id=second_assistant.id,
            status="completed",
            node_statuses=json.dumps({"agent": "succeeded"}, ensure_ascii=False),
        )
    )
    session.commit()

    response = _client(session).post(
        "/api/conversations/export/snapshots",
        json={
            "conversation_ids": [first_conversation.id, second_conversation.id],
            "include_graphs": True,
            "include_followups": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["schema_version"] == 1
    assert len(payload["conversations"]) == 2
    assert [item["conversation"]["title"] for item in payload["conversations"]] == ["第一段对话", "第二段对话"]
    assert payload["conversations"][0]["messages"][0]["content"] == "第一段用户消息"
    assert payload["conversations"][1]["messages"][1]["content"] == "第二段回答"
    assert payload["conversations"][1]["messages"][1]["graph_id"] is None
    assert payload["conversations"][1]["graphs"] == {}
