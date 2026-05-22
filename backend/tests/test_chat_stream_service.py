import json
from pathlib import Path

from sqlmodel import select

from app.models.chat_message import ChatMessage
from app.models.chat_turn import ChatTurn
from app.schemas.conversation import ConversationCreate
from app.services.chat_service import stream_conversation_chat_events
from app.services.chat_turn_service import get_chat_turn_graph_by_turn
from app.services.conversation_service import create_conversation


def test_stream_chat_creates_turn_and_messages_before_graph_finishes(
    session,
    session_factory,
    tmp_path: Path,
    monkeypatch,
):
    """流式对话开始时就应落库消息，浏览器刷新后不会丢失本轮对话。"""

    conversation = create_conversation(session, ConversationCreate(title="流式对话"))
    checkpoint_path = tmp_path / "checkpoints.db"

    def fake_stream_memory_chat_graph(**kwargs):
        user_message_id = int(kwargs["user_message_id"])
        assistant_message_id = int(kwargs["assistant_message_id"])
        yield {"event": "node", "node": "load_turn_state", "state": {}}
        yield {
            "event": "node",
            "node": "build_l3_retrieved_memory",
            "state": {
                "retrieval_query": "测试刷新恢复",
                "retrieval_debug": {
                    "planner_ms": 7,
                    "retriever_ms": 11,
                    "total_ms": 18,
                    "planner_source": "test",
                }
            },
        }
        yield {"event": "answer_delta", "node": "generate_answer", "content": "你好", "metadata": {}}
        yield {"event": "answer_delta", "node": "generate_answer", "content": "，世界", "metadata": {}}
        yield {"event": "node", "node": "persist_messages", "state": {}}
        yield {
            "event": "done",
            "node": "",
            "state": {
                "user_message_id": user_message_id,
                "assistant_message_id": assistant_message_id,
                "graph_checkpoint_id": "checkpoint-1",
                "needs_retrieval": False,
                "needs_query_rewrite": False,
                "retrieval_query": "",
                "retrieval_grade": "none",
                "retrieval_grade_reason": "",
                "retrieval_reason": "",
                "retrieved_chunks": [],
            },
        }

    monkeypatch.setattr(
        "app.services.chat_service.stream_memory_chat_graph",
        fake_stream_memory_chat_graph,
    )

    events = stream_conversation_chat_events(
        conversation.id,
        message="测试刷新恢复",
        session_factory=session_factory,
        checkpoint_path=str(checkpoint_path),
    )
    first_event = _parse_sse(next(events))

    assert first_event["event"] == "turn"
    assert first_event["data"]["turn_id"] > 0
    assert first_event["data"]["user_message"]["content"] == "测试刷新恢复"
    assert first_event["data"]["assistant_message"]["status"] == "streaming"
    assert first_event["data"]["assistant_message"]["turn_id"] == first_event["data"]["turn_id"]

    messages = session.exec(select(ChatMessage).order_by(ChatMessage.id)).all()
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[0].status == "completed"
    assert messages[1].status == "streaming"

    remaining_events = [_parse_sse(event) for event in events]
    assert [event["event"] for event in remaining_events] == [
        "node",
        "node",
        "node",
        "answer_delta",
        "answer_delta",
        "node",
        "done",
    ]

    session.expire_all()
    assistant = session.get(ChatMessage, messages[1].id)
    turn = session.get(ChatTurn, first_event["data"]["turn_id"])
    assert assistant is not None
    assert assistant.content == "你好，世界"
    assert assistant.status == "completed"
    assert turn is not None
    assert turn.status == "completed"
    assert turn.assistant_message_id == assistant.id
    done_event = remaining_events[-1]
    assert done_event["data"]["response"]["assistant_message"]["turn_id"] == first_event["data"]["turn_id"]
    debug_payload = json.loads(turn.debug_payload)
    assert debug_payload["events"]["turn_created"] >= 0
    assert debug_payload["events"]["turn_completed"] >= 0
    assert debug_payload["summary"]["first_answer_token_ms"] is not None
    assert debug_payload["summary"]["answer_token_events"] == 2
    assert debug_payload["summary"]["answer_chars"] == len("你好，世界")
    assert debug_payload["nodes"]["build_l3_retrieved_memory"]["retrieval_debug"]["planner_ms"] == 7
    assert debug_payload["nodes"]["build_l3_retrieved_memory"]["state"]["retrieval_query"] == "测试刷新恢复"


def test_chat_turn_graph_can_be_read_while_turn_is_running(session, session_factory):
    """运行中的 turn 也能通过 turn_id 打开 graph 调试视图。"""

    conversation = create_conversation(session, ConversationCreate(title="调试入口"))
    turn = ChatTurn(
        conversation_id=conversation.id,
        thread_id=f"conversation:{conversation.id}",
        status="running",
        node_statuses=json.dumps(
            {
                "load_turn_state": "succeeded",
                "dispatch_context_workers": "running",
            },
            ensure_ascii=False,
        ),
        debug_payload=json.dumps(
            {
                "events": {"turn_created": 0},
                "summary": {"first_answer_token_ms": 123},
                "nodes": {},
            },
            ensure_ascii=False,
        ),
    )
    session.add(turn)
    session.commit()
    session.refresh(turn)

    graph = get_chat_turn_graph_by_turn(
        session,
        conversation_id=conversation.id,
        turn_id=turn.id or 0,
    )

    assert graph.status == "running"
    assert graph.node_statuses["dispatch_context_workers"] == "running"
    assert graph.debug_payload["summary"]["first_answer_token_ms"] == 123
    assert "dispatch_context_workers" in graph.mermaid


def _parse_sse(raw_event: str) -> dict:
    event = ""
    data = "{}"
    for line in raw_event.strip().splitlines():
        if line.startswith("event:"):
            event = line.removeprefix("event:").strip()
        if line.startswith("data:"):
            data = line.removeprefix("data:").strip()
    return {"event": event, "data": json.loads(data)}
