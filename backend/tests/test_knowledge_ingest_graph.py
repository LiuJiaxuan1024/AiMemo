from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from sqlmodel import select

from app.agent.graphs.knowledge_ingest.graph import build_knowledge_ingest_graph, run_knowledge_ingest_graph
from app.core.config import settings
from app.jobs.models import GraphName, JobType
from app.jobs.queue import enqueue_job
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument, KnowledgeImageAsset, KnowledgeImageAssetChunk, KnowledgeSpace
from app.rag.vector_store import connect_vector_store, ensure_knowledge_vector_store


def test_knowledge_ingest_graph_writes_chunks_and_vectors(
    session,
    session_factory,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.services import knowledge_document_service

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(settings, "embedding_dimensions", 4)
    monkeypatch.setattr(knowledge_document_service, "KNOWLEDGE_DATA_ROOT", tmp_path / "knowledge")
    ensure_knowledge_vector_store()

    document = _make_document(session, tmp_path, "guide.md", "# 标题\n\n第一段。\n\n第二段。")
    job = enqueue_job(
        session,
        job_type=JobType.KNOWLEDGE_INGEST.value,
        graph_name=GraphName.KNOWLEDGE_INGEST.value,
        payload={"document_id": document.id, "content_hash": document.content_hash},
    )
    session.commit()
    session.refresh(job)

    def fake_embeddings(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0, float(index)] for index, _ in enumerate(texts)]

    run_knowledge_ingest_graph(
        job,
        session_factory=session_factory,
        checkpoint_path=str(tmp_path / "checkpoints.db"),
        embedding_generator=fake_embeddings,
        image_text_extractor=lambda asset: f"{asset.location_label} 展示 Amazon DynamoDB 的关键概念。",
    )

    session.refresh(document)
    chunks = session.exec(select(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id)).all()
    assert document.status == "ready"
    assert document.chunk_count == len(chunks)
    assert len(chunks) >= 1
    assert all(chunk.embedding_status == "completed" for chunk in chunks)
    assert chunks[0].heading_path is not None

    with connect_vector_store() as connection:
        rows = connection.execute("select rowid from vec_knowledge_chunks").fetchall()
    assert rows == [(chunk.id,) for chunk in chunks]


def test_knowledge_ingest_graph_records_image_chunk_stats(
    session,
    session_factory,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.services import knowledge_document_service

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(settings, "embedding_dimensions", 4)
    monkeypatch.setattr(knowledge_document_service, "KNOWLEDGE_DATA_ROOT", tmp_path / "knowledge")
    ensure_knowledge_vector_store()

    document = _make_document(session, tmp_path, "image-guide.md", "# 图示\n\n![架构图](arch.png)\n\n正文。")
    job = enqueue_job(
        session,
        job_type=JobType.KNOWLEDGE_INGEST.value,
        graph_name=GraphName.KNOWLEDGE_INGEST.value,
        payload={"document_id": document.id, "content_hash": document.content_hash},
    )
    session.commit()
    session.refresh(job)

    def fake_embeddings(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0, float(index)] for index, _ in enumerate(texts)]

    run_knowledge_ingest_graph(
        job,
        session_factory=session_factory,
        checkpoint_path=str(tmp_path / "checkpoints.db"),
        embedding_generator=fake_embeddings,
        image_text_extractor=lambda asset: f"{asset.location_label} 展示 Amazon DynamoDB 的关键概念。",
    )

    session.refresh(document)
    chunks = session.exec(select(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id)).all()
    image_chunks = [chunk for chunk in chunks if chunk.metadata_json and "image_asset" in chunk.metadata_json]
    image_assets = session.exec(select(KnowledgeImageAsset).where(KnowledgeImageAsset.document_id == document.id)).all()

    assert document.status == "ready"
    assert document.image_asset_count == 1
    assert document.image_asset_processed_count == 1
    assert document.image_asset_failed_count == 0
    assert document.image_text_chunk_count == len(image_chunks) == 1
    assert document.text_chunk_count == document.chunk_count - document.image_text_chunk_count
    assert "Amazon DynamoDB" in image_chunks[0].text
    assert len(image_assets) == 1
    assert image_assets[0].status == "completed"
    assert image_assets[0].retryable is False
    assert image_assets[0].attempt_count == 1
    links = session.exec(
        select(KnowledgeImageAssetChunk).where(KnowledgeImageAssetChunk.image_asset_id == image_assets[0].id)
    ).all()
    assert [link.chunk_id for link in links] == [image_chunks[0].id]


def test_knowledge_ingest_graph_records_retryable_failed_image_assets(
    session,
    session_factory,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.services import knowledge_document_service
    from app.services.knowledge_image_text_service import ImageTextExtractionError

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(settings, "embedding_dimensions", 4)
    monkeypatch.setattr(knowledge_document_service, "KNOWLEDGE_DATA_ROOT", tmp_path / "knowledge")
    ensure_knowledge_vector_store()

    document = _make_document(session, tmp_path, "image-timeout.md", "# 图示\n\n![架构图](arch.png)\n\n正文。")
    job = enqueue_job(
        session,
        job_type=JobType.KNOWLEDGE_INGEST.value,
        graph_name=GraphName.KNOWLEDGE_INGEST.value,
        payload={"document_id": document.id, "content_hash": document.content_hash},
    )
    session.commit()
    session.refresh(job)

    def fake_embeddings(texts: list[str]) -> list[list[float]]:
        return [[1.0, 0.0, 0.0, float(index)] for index, _ in enumerate(texts)]

    def fail_image_text(_asset) -> str:
        raise ImageTextExtractionError("DASHSCOPE_REQUEST_TIMEOUT", "timeout", retryable=True)

    run_knowledge_ingest_graph(
        job,
        session_factory=session_factory,
        checkpoint_path=str(tmp_path / "checkpoints.db"),
        embedding_generator=fake_embeddings,
        image_text_extractor=fail_image_text,
    )

    session.refresh(document)
    image_assets = session.exec(select(KnowledgeImageAsset).where(KnowledgeImageAsset.document_id == document.id)).all()
    links = session.exec(select(KnowledgeImageAssetChunk)).all()

    assert document.status == "ready"
    assert document.image_asset_count == 1
    assert document.image_asset_processed_count == 0
    assert document.image_asset_failed_count == 1
    assert document.image_text_chunk_count == 0
    assert len(image_assets) == 1
    assert image_assets[0].status == "failed"
    assert image_assets[0].retryable is True
    assert image_assets[0].error_code == "DASHSCOPE_REQUEST_TIMEOUT"
    assert image_assets[0].attempt_count == 1
    assert links == []


def test_knowledge_image_retry_job_rebuilds_only_failed_image_chunk(
    session,
    session_factory,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.rag.document_parsers import parse_document_file
    from app.services import knowledge_document_service
    from app.services import knowledge_image_asset_service

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(settings, "embedding_dimensions", 4)
    monkeypatch.setattr(knowledge_document_service, "KNOWLEDGE_DATA_ROOT", tmp_path / "knowledge")
    monkeypatch.setattr(
        knowledge_image_asset_service,
        "default_image_text_extractor",
        lambda asset: f"{asset.location_label} 重试成功，图片描述了云端对象存储。",
    )
    monkeypatch.setattr(
        knowledge_image_asset_service,
        "embed_texts",
        lambda texts: [[0.0, 1.0, 0.0, float(index)] for index, _ in enumerate(texts)],
    )
    ensure_knowledge_vector_store()

    document = _make_document(session, tmp_path, "retry-image.md", "# 图示\n\n![架构图](arch.png)\n\n正文。")
    source_path = knowledge_document_service.KNOWLEDGE_DATA_ROOT / (document.storage_path or "")
    (source_path.parent / "arch.png").write_bytes(b"fake-image-bytes")
    parsed = parse_document_file(source_path)
    image_asset = parsed.image_assets[0]
    asset_row = KnowledgeImageAsset(
        space_id=document.space_id,
        document_id=document.id or 0,
        asset_id=image_asset.asset_id,
        asset_uid="stale-uid",
        parser=image_asset.parser,
        location_label=image_asset.location_label,
        status="failed",
        retryable=True,
        attempt_count=1,
        error_code="DASHSCOPE_REQUEST_TIMEOUT",
        error_message="timeout",
    )
    session.add(asset_row)
    session.flush()
    assert asset_row.id is not None
    document.image_asset_count = 1
    document.image_asset_failed_count = 1
    session.add(document)
    job = enqueue_job(
        session,
        job_type=JobType.KNOWLEDGE_IMAGE_RETRY.value,
        graph_name=GraphName.KNOWLEDGE_IMAGE_RETRY.value,
        payload={"document_id": document.id, "image_asset_ids": [asset_row.id]},
    )
    session.commit()
    session.refresh(job)

    knowledge_image_asset_service.run_knowledge_image_retry_job(job, session_factory=session_factory)

    session.refresh(document)
    session.refresh(asset_row)
    chunks = session.exec(select(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id)).all()
    links = session.exec(
        select(KnowledgeImageAssetChunk).where(KnowledgeImageAssetChunk.image_asset_id == asset_row.id)
    ).all()

    assert asset_row.status == "completed"
    assert asset_row.retryable is False
    assert asset_row.error_message is None
    assert asset_row.attempt_count == 2
    assert len(chunks) == 1
    assert "对象存储" in chunks[0].text
    assert [link.chunk_id for link in links] == [chunks[0].id]
    assert document.image_asset_failed_count == 0
    assert document.image_text_chunk_count == 1

    with connect_vector_store() as connection:
        rows = connection.execute("select rowid from vec_knowledge_chunks").fetchall()
    assert rows == [(chunks[0].id,)]


def test_knowledge_ingest_graph_resumes_after_generate_without_reembedding(
    session,
    session_factory,
    tmp_path: Path,
    monkeypatch,
) -> None:
    from app.services import knowledge_document_service

    monkeypatch.setattr(settings, "database_url", f"sqlite:///{tmp_path / 'test.db'}")
    monkeypatch.setattr(settings, "embedding_dimensions", 4)
    monkeypatch.setattr(knowledge_document_service, "KNOWLEDGE_DATA_ROOT", tmp_path / "knowledge")
    ensure_knowledge_vector_store()

    document = _make_document(session, tmp_path, "resume.txt", "恢复测试内容。")
    job = enqueue_job(
        session,
        job_type=JobType.KNOWLEDGE_INGEST.value,
        graph_name=GraphName.KNOWLEDGE_INGEST.value,
        payload={"document_id": document.id, "content_hash": document.content_hash},
    )
    session.commit()
    session.refresh(job)

    calls: list[list[str]] = []

    def fake_embeddings(texts: list[str]) -> list[list[float]]:
        calls.append(texts)
        return [[0.0, 1.0, 0.0, 0.0] for _ in texts]

    checkpoint_path = tmp_path / "checkpoints.db"
    run_knowledge_ingest_graph(
        job,
        session_factory=session_factory,
        checkpoint_path=str(checkpoint_path),
        embedding_generator=fake_embeddings,
        interrupt_after=["generate_embeddings"],
    )

    with SqliteSaver.from_conn_string(str(checkpoint_path)) as checkpointer:
        graph = build_knowledge_ingest_graph(
            session_factory=session_factory,
            embedding_generator=fake_embeddings,
        ).compile(checkpointer=checkpointer)
        snapshot = graph.get_state({"configurable": {"thread_id": job.thread_id}})
        assert snapshot.next == ("write_vector_index",)

    run_knowledge_ingest_graph(
        job,
        session_factory=session_factory,
        checkpoint_path=str(checkpoint_path),
        embedding_generator=fake_embeddings,
    )

    session.refresh(document)
    assert len(calls) == 1
    assert document.status == "ready"


def _make_document(session, tmp_path: Path, filename: str, content: str) -> KnowledgeDocument:
    from app.services import knowledge_document_service

    space = KnowledgeSpace(name="测试知库")
    session.add(space)
    session.flush()
    assert space.id is not None
    document = KnowledgeDocument(
        space_id=space.id,
        title=Path(filename).stem,
        source_type="file",
        original_filename=filename,
        storage_path=f"files/{space.id}/1/original-{filename}",
        content_hash=f"hash-{filename}",
        parser=Path(filename).suffix.lstrip("."),
        status="pending",
    )
    session.add(document)
    session.flush()
    assert document.id is not None
    document.storage_path = f"files/{space.id}/{document.id}/original-{filename}"
    file_path = knowledge_document_service.KNOWLEDGE_DATA_ROOT / document.storage_path
    file_path.parent.mkdir(parents=True, exist_ok=True)
    file_path.write_text(content, encoding="utf-8")
    session.add(document)
    session.commit()
    session.refresh(document)
    return document
