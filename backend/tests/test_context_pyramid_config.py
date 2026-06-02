from app.agent.graphs.memory_chat.nodes import build_l1_recent_messages_node
from app.core.config import Settings, settings


def test_settings_builds_configured_context_pyramid_budget() -> None:
    configured = Settings(
        context_pyramid_core_memory_tokens=11,
        context_pyramid_retrieved_memory_tokens=22,
        context_pyramid_summary_tokens=33,
        context_pyramid_conversation_window_tokens=44,
        context_pyramid_recent_message_tokens=55,
        context_pyramid_weak_retrieval_max_chunks=6,
    )

    budget = configured.context_pyramid_budget

    assert budget.core_memory_tokens == 11
    assert budget.retrieved_memory_tokens == 22
    assert budget.summary_tokens == 33
    assert budget.conversation_window_tokens == 44
    assert budget.recent_message_tokens == 55
    assert budget.weak_retrieval_max_chunks == 6


def test_memory_chat_context_workers_use_configured_budget(monkeypatch) -> None:
    monkeypatch.setattr(settings, "context_pyramid_recent_message_tokens", 123)

    update = build_l1_recent_messages_node()({"recent_messages": []})

    assert update["context_l1_layer"]["budget_tokens"] == 123
