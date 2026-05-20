from pathlib import Path

from app.services.elf_chat_service import stream_elf_chat_events
from app.services.elf_event_service import elf_event_service


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
        "app.services.chat_service.stream_memory_chat_graph",
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
