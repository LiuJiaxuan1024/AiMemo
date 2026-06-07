from pathlib import Path

from app.core.config import settings
from app.models.note import Note
from app.models.note_chunk import NoteChunk
from app.rag.search import search_notes, search_notes_keyword
from app.rag.vector_store import ensure_vector_store, upsert_chunk_embedding


def test_search_notes_returns_nearest_note_chunks(session, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(settings, "embedding_dimensions", 4)
    ensure_vector_store()

    food_note = Note(title="午餐计划", content="今天中午想吃炸鸡。", embedding_status="completed")
    travel_note = Note(title="旅行计划", content="周末想去故宫。", embedding_status="completed")
    session.add(food_note)
    session.add(travel_note)
    session.flush()

    food_chunk = NoteChunk(
        note_id=food_note.id or 0,
        chunk_index=0,
        content="今天中午想吃炸鸡。",
        content_hash="food",
        token_count=8,
        embedding_status="completed",
    )
    travel_chunk = NoteChunk(
        note_id=travel_note.id or 0,
        chunk_index=0,
        content="周末想去故宫。",
        content_hash="travel",
        token_count=7,
        embedding_status="completed",
    )
    session.add(food_chunk)
    session.add(travel_chunk)
    session.commit()
    session.refresh(food_chunk)
    session.refresh(travel_chunk)

    upsert_chunk_embedding(food_chunk.id or 0, [1.0, 0.0, 0.0, 0.0])
    upsert_chunk_embedding(travel_chunk.id or 0, [0.0, 1.0, 0.0, 0.0])

    def fake_embeddings(texts: list[str]) -> list[list[float]]:
        assert texts == ["我之前说过想吃什么？"]
        return [[1.0, 0.0, 0.0, 0.0]]

    results = search_notes(
        session,
        query="我之前说过想吃什么？",
        limit=2,
        embedding_generator=fake_embeddings,
    )

    assert [result.note_title for result in results] == ["午餐计划", "旅行计划"]
    assert results[0].content == "今天中午想吃炸鸡。"
    assert results[0].distance <= results[1].distance
    assert results[0].score >= results[1].score


def test_search_notes_ignores_deleted_notes(session, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(settings, "embedding_dimensions", 4)
    ensure_vector_store()

    active_note = Note(title="生效", content="这条可以被检索。", embedding_status="completed")
    deleted_note = Note(
        title="最近删除",
        content="这条已经删除，不应该被检索。",
        embedding_status="completed",
        status="deleted",
    )
    session.add(active_note)
    session.add(deleted_note)
    session.flush()

    active_chunk = NoteChunk(
        note_id=active_note.id or 0,
        chunk_index=0,
        content="这条可以被检索。",
        content_hash="active",
        token_count=8,
        embedding_status="completed",
    )
    deleted_chunk = NoteChunk(
        note_id=deleted_note.id or 0,
        chunk_index=0,
        content="这条已经删除，不应该被检索。",
        content_hash="deleted",
        token_count=10,
        embedding_status="completed",
    )
    session.add(active_chunk)
    session.add(deleted_chunk)
    session.commit()
    session.refresh(active_chunk)
    session.refresh(deleted_chunk)

    upsert_chunk_embedding(active_chunk.id or 0, [0.8, 0.0, 0.0, 0.0])
    upsert_chunk_embedding(deleted_chunk.id or 0, [1.0, 0.0, 0.0, 0.0])

    results = search_notes(
        session,
        query="删除的内容是什么？",
        limit=2,
        embedding_generator=lambda _: [[1.0, 0.0, 0.0, 0.0]],
    )

    assert [result.note_title for result in results] == ["生效"]


def test_search_notes_ignores_blank_query(session):
    calls = 0

    def fake_embeddings(texts: list[str]) -> list[list[float]]:
        nonlocal calls
        calls += 1
        return [[1.0, 0.0, 0.0, 0.0]]

    assert search_notes(session, query="  ", embedding_generator=fake_embeddings) == []
    assert calls == 0


def test_search_notes_keyword_returns_lightweight_note_candidates(session):
    active_note = Note(
        title="热力学复习",
        content="水结冰时关注相变、潜热和分子排列变化。",
        summary="相变复习",
        tags="physics",
        status="active",
    )
    deleted_note = Note(
        title="删除的热力学笔记",
        content="删除内容不应该进入召回。",
        status="deleted",
    )
    session.add(active_note)
    session.add(deleted_note)
    session.flush()
    active_chunk = NoteChunk(
        note_id=active_note.id or 0,
        chunk_index=0,
        content="水结冰时关注相变、潜热和分子排列变化。",
        content_hash="active-keyword",
        token_count=14,
        embedding_status="pending",
    )
    deleted_chunk = NoteChunk(
        note_id=deleted_note.id or 0,
        chunk_index=0,
        content="水结冰也可能出现在删除笔记里。",
        content_hash="deleted-keyword",
        token_count=12,
    )
    session.add(active_chunk)
    session.add(deleted_chunk)
    session.commit()

    results = search_notes_keyword(session, query="水为什么会结冰？", limit=3)

    assert [result.note_title for result in results] == ["热力学复习"]
    assert results[0].score >= 0.42
    assert results[0].content == "水结冰时关注相变、潜热和分子排列变化。"
