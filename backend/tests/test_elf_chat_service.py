import json
from datetime import timedelta
from pathlib import Path

from app.models.chat_turn import ChatTurn
from app.models.note import utc_now
from app.api.elf import get_elf_turn_graph_api
from app.services import chat_turn_buffer
from app.services.elf_chat_service import (
    _elf_chat_run_lock,
    get_elf_chat_status,
    get_or_create_elf_conversation_in_session,
    get_or_create_elf_conversation,
    stream_elf_chat_events,
)
from app.services.elf_event_service import elf_event_service
from app.services.elf_runtime_state_service import get_elf_runtime_state, update_elf_runtime_state


def test_elf_chat_stream_reuses_memory_chat_without_status_elf_events(
    session_factory,
    tmp_path: Path,
    monkeypatch,
) -> None:
    """外置精灵聊天应复用 Memory Chat Graph，但不播报内部工作状态。"""

    def fake_stream_memory_chat_graph(**kwargs):
        yield {
            "event": "answer_delta",
            "node": "generate_answer",
            "content": "你好，我在。",
            "metadata": {},
        }
        yield {
            "event": "done",
            "node": "",
            "state": {
                "user_message_id": int(kwargs["user_message_id"]),
                "assistant_message_id": int(kwargs["assistant_message_id"]),
                "graph_checkpoint_id": "checkpoint-elf-1",
                "needs_retrieval": False,
                "needs_query_rewrite": False,
                "retrieval_query": "",
                "retrieval_grade": "none",
                "retrieval_grade_reason": "",
                "retrieval_reason": "",
                "retrieved_chunks": [],
            },
        }

    elf_event_service.clear()
    monkeypatch.setattr(
        "app.agent.graphs.memory_chat.graph.stream_memory_chat_graph",
        fake_stream_memory_chat_graph,
    )

    events = list(
        stream_elf_chat_events(
            message="在吗",
            session_factory=session_factory,
            checkpoint_path=str(tmp_path / "checkpoints.db"),
        )
    )

    assert any("event: answer_delta" in event for event in events)
    assert any("event: done" in event for event in events)
    assert elf_event_service.list_after(0) == []


def test_elf_chat_stream_rejects_when_global_lock_is_busy(session_factory) -> None:
    assert _elf_chat_run_lock.acquire(blocking=False)
    try:
        events = list(stream_elf_chat_events(message="再问一句", session_factory=session_factory))
    finally:
        _elf_chat_run_lock.release()

    assert any("ELF_CHAT_BUSY" in event for event in events)


def test_elf_chat_status_recovers_orphan_running_turn(session_factory) -> None:
    chat_turn_buffer.reset_for_tests()
    conversation = get_or_create_elf_conversation(session_factory=session_factory)
    assert conversation.id is not None

    with session_factory() as session:
        turn = ChatTurn(
            conversation_id=conversation.id,
            thread_id=f"conversation:{conversation.id}",
            status="running",
            node_statuses=json.dumps({"agent": "running"}, ensure_ascii=False),
            updated_at=utc_now() - timedelta(seconds=1),
        )
        session.add(turn)
        session.commit()
        turn_id = turn.id

    status = get_elf_chat_status(session_factory=session_factory)

    assert status["busy"] is False
    assert status["status"] == "idle"
    assert status["message"] == ""
    runtime_status = get_elf_runtime_state(session_factory=session_factory)
    assert runtime_status.status == "failed"
    assert runtime_status.turn_id == turn_id
    with session_factory() as session:
        recovered_turn = session.get(ChatTurn, turn_id)
        assert recovered_turn is not None
        assert recovered_turn.status == "failed"


def test_elf_chat_status_reports_interrupted_as_waiting_for_choice(session_factory) -> None:
    chat_turn_buffer.reset_for_tests()
    conversation = get_or_create_elf_conversation(session_factory=session_factory)
    assert conversation.id is not None
    pending_interrupt = {
        "question": "请选择目录",
        "options": [{"id": "home", "label": "Home", "value": "home"}],
    }

    with session_factory() as session:
        turn = ChatTurn(
            conversation_id=conversation.id,
            thread_id=f"conversation:{conversation.id}",
            status="interrupted",
            node_statuses=json.dumps({"tools": "interrupted"}, ensure_ascii=False),
            debug_payload=json.dumps({"pending_interrupt": pending_interrupt}, ensure_ascii=False),
        )
        session.add(turn)
        session.commit()
        turn_id = turn.id
        assert turn_id is not None
        update_elf_runtime_state(
            session,
            status="waiting_user_input",
            conversation_id=conversation.id,
            turn_id=turn_id,
            pending_interrupt=pending_interrupt,
        )

    status = get_elf_chat_status(session_factory=session_factory)

    assert status["busy"] is True
    assert status["status"] == "interrupted"
    assert "等你选择" in status["message"]
    assert "后台处理中" not in status["message"]


def test_elf_chat_status_recovers_stale_interrupted_turn_without_runtime_owner(session_factory) -> None:
    chat_turn_buffer.reset_for_tests()
    conversation = get_or_create_elf_conversation(session_factory=session_factory)
    assert conversation.id is not None

    with session_factory() as session:
        turn = ChatTurn(
            conversation_id=conversation.id,
            thread_id=f"conversation:{conversation.id}",
            status="interrupted",
            node_statuses=json.dumps({"tools": "interrupted"}, ensure_ascii=False),
            debug_payload=json.dumps(
                {
                    "pending_interrupt": {
                        "question": "旧选择",
                        "options": [{"id": "old", "label": "旧选项", "value": "old"}],
                    }
                },
                ensure_ascii=False,
            ),
        )
        session.add(turn)
        session.commit()
        turn_id = turn.id

    status = get_elf_chat_status(session_factory=session_factory)

    assert status["busy"] is False
    assert status["status"] == "idle"
    runtime_status = get_elf_runtime_state(session_factory=session_factory)
    assert runtime_status.status == "failed"
    assert runtime_status.busy is False
    with session_factory() as session:
        recovered_turn = session.get(ChatTurn, turn_id)
        assert recovered_turn is not None
        assert recovered_turn.status == "failed"


def test_elf_turn_graph_api_exposes_saved_context(session) -> None:
    conversation = get_or_create_elf_conversation_in_session(session)
    assert conversation.id is not None
    context_layers = [
        {
            "level": 0,
            "name": "Current input",
            "content": "用户刚才问精灵的问题",
            "budget_tokens": 800,
            "used_tokens": 12,
            "note": "",
            "kind": "layer",
        }
    ]
    turn = ChatTurn(
        conversation_id=conversation.id,
        thread_id=conversation.langgraph_thread_id,
        status="completed",
        node_statuses=json.dumps(
            {
                "load_turn_state": "succeeded",
                "generate_elf_bubble_answer": "succeeded",
                "persist_messages": "succeeded",
            },
            ensure_ascii=False,
        ),
        context_layers=json.dumps(context_layers, ensure_ascii=False),
        debug_payload=json.dumps({"summary": {"answer_chars": 8}, "nodes": {}}, ensure_ascii=False),
    )
    session.add(turn)
    session.commit()
    session.refresh(turn)

    graph = get_elf_turn_graph_api(turn.id or 0, session=session)

    assert graph.context_layers == context_layers
    assert "generate_elf_bubble_answer" in graph.node_statuses
    assert "generate_elf_bubble_answer" in graph.mermaid
