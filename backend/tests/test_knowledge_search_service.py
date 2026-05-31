from collections.abc import Generator
import json
from pathlib import Path

from fastapi.testclient import TestClient
from sqlmodel import Session

from app.core.config import settings
from app.core.database import get_session
from app.main import create_app
from app.models.conversation import Conversation
from app.models.knowledge import ConversationKnowledgeMount, KnowledgeChunk, KnowledgeDocument, KnowledgeSpace
from app.rag.vector_store import ensure_knowledge_vector_store, upsert_knowledge_chunk_embedding
from app.services.knowledge_search_service import NEED_KNOWLEDGE_MOUNT, search_knowledge, search_mounted_knowledge


def test_search_knowledge_hybrid_filters_and_merges(session: Session, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(settings, "embedding_dimensions", 4)
    ensure_knowledge_vector_store()

    ready_chunk, archived_chunk, pending_chunk = _seed_search_data(session)
    upsert_knowledge_chunk_embedding(ready_chunk.id or 0, [1.0, 0.0, 0.0, 0.0])
    upsert_knowledge_chunk_embedding(archived_chunk.id or 0, [1.0, 0.0, 0.0, 0.0])
    upsert_knowledge_chunk_embedding(pending_chunk.id or 0, [1.0, 0.0, 0.0, 0.0])

    result = search_knowledge(
        session,
        query="Zenoh publisher",
        space_ids=[ready_chunk.space_id, archived_chunk.space_id],
        top_k=5,
        mode="hybrid",
        embedding_generator=lambda _: [[1.0, 0.0, 0.0, 0.0]],
    )

    assert result.status == "ok"
    assert [item.chunk_id for item in result.results] == [ready_chunk.id]
    assert result.results[0].score_source == "hybrid"
    assert result.results[0].heading_path == ["迁移"]


def test_search_knowledge_keyword_mode_without_embedding_call(session: Session) -> None:
    ready_chunk, _, _ = _seed_search_data(session)
    calls = 0

    def fake_embeddings(_: list[str]) -> list[list[float]]:
        nonlocal calls
        calls += 1
        return [[1.0, 0.0, 0.0, 0.0]]

    result = search_knowledge(
        session,
        query="publisher",
        space_ids=[ready_chunk.space_id],
        top_k=3,
        mode="keyword",
        embedding_generator=fake_embeddings,
    )

    assert calls == 0
    assert [item.chunk_id for item in result.results] == [ready_chunk.id]
    assert result.results[0].score_source == "keyword"


def test_search_mounted_knowledge_uses_only_mounted_spaces(session: Session, tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(settings, "embedding_dimensions", 4)
    ensure_knowledge_vector_store()

    mounted_chunk, unmounted_chunk, _ = _seed_search_data(session)
    upsert_knowledge_chunk_embedding(mounted_chunk.id or 0, [1.0, 0.0, 0.0, 0.0])
    upsert_knowledge_chunk_embedding(unmounted_chunk.id or 0, [0.9, 0.0, 0.0, 0.0])

    conversation = Conversation(title="知库挂载测试", langgraph_thread_id="conversation:test")
    session.add(conversation)
    session.flush()
    session.add(
        ConversationKnowledgeMount(
            conversation_id=conversation.id or 0,
            space_id=mounted_chunk.space_id,
        )
    )
    session.commit()

    result = search_mounted_knowledge(
        session,
        conversation_id=conversation.id or 0,
        query="Zenoh",
        embedding_generator=lambda _: [[1.0, 0.0, 0.0, 0.0]],
    )

    assert [item.chunk_id for item in result.results] == [mounted_chunk.id]


def test_search_mounted_knowledge_requires_mount(session: Session) -> None:
    conversation = Conversation(title="未挂载", langgraph_thread_id="conversation:none")
    session.add(conversation)
    session.commit()

    result = search_mounted_knowledge(
        session,
        conversation_id=conversation.id or 0,
        query="Zenoh",
        embedding_generator=lambda _: [[1.0, 0.0, 0.0, 0.0]],
    )

    assert result.status == NEED_KNOWLEDGE_MOUNT
    assert result.results == []


def test_knowledge_search_api(session: Session) -> None:
    ready_chunk, _, _ = _seed_search_data(session)
    app = create_app()

    def override_get_session() -> Generator[Session, None, None]:
        yield session

    app.dependency_overrides[get_session] = override_get_session
    client = TestClient(app)

    response = client.post(
        "/api/knowledge/search",
        json={"query": "publisher", "space_id": ready_chunk.space_id, "top_k": 3, "mode": "keyword"},
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert [item["chunk_id"] for item in payload["results"]] == [ready_chunk.id]


def _seed_search_data(session: Session) -> tuple[KnowledgeChunk, KnowledgeChunk, KnowledgeChunk]:
    active_space = KnowledgeSpace(name="Zenoh 资料")
    archived_space = KnowledgeSpace(name="旧资料", status="archived")
    pending_space = KnowledgeSpace(name="处理中资料")
    session.add(active_space)
    session.add(archived_space)
    session.add(pending_space)
    session.flush()

    ready_doc = KnowledgeDocument(
        space_id=active_space.id or 0,
        title="Publisher 迁移",
        source_type="file",
        original_filename="publisher.md",
        content_hash="ready",
        status="ready",
    )
    archived_doc = KnowledgeDocument(
        space_id=archived_space.id or 0,
        title="归档文档",
        source_type="file",
        original_filename="archived.md",
        content_hash="archived",
        status="ready",
    )
    pending_doc = KnowledgeDocument(
        space_id=pending_space.id or 0,
        title="未完成文档",
        source_type="file",
        original_filename="pending.md",
        content_hash="pending",
        status="pending",
    )
    session.add(ready_doc)
    session.add(archived_doc)
    session.add(pending_doc)
    session.flush()

    ready_chunk = KnowledgeChunk(
        space_id=active_space.id or 0,
        document_id=ready_doc.id or 0,
        chunk_index=0,
        text="Zenoh publisher 迁移需要检查 topic 与 session 生命周期。",
        heading_path=json.dumps(["迁移"], ensure_ascii=False),
        content_hash="ready-chunk",
        token_count=12,
        embedding_status="completed",
    )
    archived_chunk = KnowledgeChunk(
        space_id=archived_space.id or 0,
        document_id=archived_doc.id or 0,
        chunk_index=0,
        text="Zenoh publisher archived chunk.",
        content_hash="archived-chunk",
        token_count=8,
        embedding_status="completed",
    )
    pending_chunk = KnowledgeChunk(
        space_id=pending_space.id or 0,
        document_id=pending_doc.id or 0,
        chunk_index=0,
        text="Zenoh publisher pending chunk.",
        content_hash="pending-chunk",
        token_count=8,
        embedding_status="completed",
    )
    session.add(ready_chunk)
    session.add(archived_chunk)
    session.add(pending_chunk)
    session.commit()
    session.refresh(ready_chunk)
    session.refresh(archived_chunk)
    session.refresh(pending_chunk)
    return ready_chunk, archived_chunk, pending_chunk
