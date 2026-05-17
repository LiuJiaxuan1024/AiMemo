import pytest
from fastapi import HTTPException

from app.models.long_term_memory import LongTermMemory
from app.services.long_term_memory_service import list_core_memories
from app.services.memory_service import (
    archive_memory,
    build_memory_content_hash,
    list_memories,
    update_memory,
)
from app.schemas.memory import MemoryUpdate


def test_list_memories_defaults_to_active_l4_sorted(session):
    low = _add_memory(session, "用户喜欢黑咖啡。", importance=0.4)
    high = _add_memory(session, "用户不吃香菜。", importance=0.9)
    _add_memory(session, "停用记忆。", status="archived", importance=1.0)
    _add_memory(session, "非 L4 记忆。", level=3, importance=1.0)

    memories = list_memories(session)

    assert [memory.id for memory in memories] == [high.id, low.id]


def test_update_memory_recomputes_hash_when_content_or_category_changes(session):
    memory = _add_memory(session, "用户喜欢咖啡。", category="preference")
    old_hash = memory.content_hash

    updated = update_memory(
        session,
        memory.id or 0,
        MemoryUpdate(
            category="goal",
            content="用户的长期目标是写完 Ai 记。",
            summary="写完 Ai 记",
            importance=0.95,
            confidence=0.9,
        ),
    )

    assert updated.category == "goal"
    assert updated.content == "用户的长期目标是写完 Ai 记。"
    assert updated.summary == "写完 Ai 记"
    assert updated.importance == 0.95
    assert updated.confidence == 0.9
    assert updated.content_hash != old_hash
    assert updated.content_hash == build_memory_content_hash(updated.category, updated.content)


def test_update_memory_rejects_invalid_fields(session):
    memory = _add_memory(session, "用户不吃香菜。")

    with pytest.raises(HTTPException) as category_error:
        update_memory(session, memory.id or 0, MemoryUpdate(category="bad"))
    with pytest.raises(HTTPException) as status_error:
        update_memory(session, memory.id or 0, MemoryUpdate(status="deleted"))
    with pytest.raises(HTTPException) as score_error:
        update_memory(session, memory.id or 0, MemoryUpdate(importance=1.5))
    with pytest.raises(HTTPException) as content_error:
        update_memory(session, memory.id or 0, MemoryUpdate(content="   "))

    assert category_error.value.status_code == 400
    assert status_error.value.status_code == 400
    assert score_error.value.status_code == 400
    assert content_error.value.status_code == 400


def test_archive_memory_disables_and_memory_can_be_reactivated(session):
    memory = _add_memory(session, "用户不吃香菜。")

    archived = archive_memory(session, memory.id or 0)

    assert archived.status == "archived"
    assert list_core_memories(session) == []

    active = update_memory(session, memory.id or 0, MemoryUpdate(status="active"))

    assert active.status == "active"
    assert [memory.content for memory in list_core_memories(session)] == ["用户不吃香菜。"]


def _add_memory(
    session,
    content: str,
    *,
    category: str = "preference",
    level: int = 4,
    status: str = "active",
    importance: float = 0.8,
    confidence: float = 0.9,
) -> LongTermMemory:
    memory = LongTermMemory(
        level=level,
        category=category,
        content=content,
        summary=content[:20],
        importance=importance,
        confidence=confidence,
        status=status,
        content_hash=build_memory_content_hash(category, content),
    )
    session.add(memory)
    session.commit()
    session.refresh(memory)
    return memory
