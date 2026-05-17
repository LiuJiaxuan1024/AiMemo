from pathlib import Path

from app.core.config import settings
from app.models.note import Note
from app.models.note_chunk import NoteChunk
from app.rag.search import search_notes
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


def test_search_notes_ignores_blank_query(session):
    calls = 0

    def fake_embeddings(texts: list[str]) -> list[list[float]]:
        nonlocal calls
        calls += 1
        return [[1.0, 0.0, 0.0, 0.0]]

    assert search_notes(session, query="  ", embedding_generator=fake_embeddings) == []
    assert calls == 0

