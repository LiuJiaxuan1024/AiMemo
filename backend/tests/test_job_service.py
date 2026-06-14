from app.agent.graphs.registry import get_job_graph_view
from app.jobs.models import GraphName, JobType
from app.jobs.queue import enqueue_job
from app.models.knowledge import KnowledgeDocument, KnowledgeImageAsset, KnowledgeSpace
from app.services.job_service import get_job_graph, list_jobs


def test_list_jobs_returns_decoded_payload(session):
    job = enqueue_job(
        session,
        job_type=JobType.NOTE_METADATA.value,
        graph_name=GraphName.NOTE_METADATA.value,
        payload={"note_id": 123},
    )
    session.commit()

    jobs = list_jobs(session)

    assert jobs[0].id == job.id
    assert jobs[0].payload == {"note_id": 123}


def test_job_graph_view_returns_mermaid(session):
    job = enqueue_job(
        session,
        job_type=JobType.NOTE_METADATA.value,
        graph_name=GraphName.NOTE_METADATA.value,
        payload={"note_id": 123},
    )
    session.commit()
    session.refresh(job)

    graph = get_job_graph(session, job.id or 0)

    assert graph.graph_name == GraphName.NOTE_METADATA.value
    assert "load_note" in graph.mermaid
    assert "generate_metadata" in graph.mermaid
    assert "write_metadata" in graph.mermaid


def test_summary_job_graph_view_returns_mermaid(session):
    job = enqueue_job(
        session,
        job_type=JobType.CONVERSATION_SUMMARY.value,
        graph_name=GraphName.CONVERSATION_SUMMARY.value,
        payload={"conversation_id": 1},
    )
    session.commit()
    session.refresh(job)

    graph = get_job_graph(session, job.id or 0)

    assert graph.graph_name == GraphName.CONVERSATION_SUMMARY.value
    assert "load_summary_inputs" in graph.mermaid
    assert "summarize_messages" in graph.mermaid
    assert "persist_summary" in graph.mermaid


def test_memory_job_graph_view_returns_mermaid(session):
    job = enqueue_job(
        session,
        job_type=JobType.CONVERSATION_MEMORY.value,
        graph_name=GraphName.CONVERSATION_MEMORY.value,
        payload={
            "conversation_id": 1,
            "user_message_id": 1,
            "assistant_message_id": 2,
        },
    )
    session.commit()
    session.refresh(job)

    graph = get_job_graph(session, job.id or 0)

    assert graph.graph_name == GraphName.CONVERSATION_MEMORY.value
    assert "load_memory_source" in graph.mermaid
    assert "extract_memories" in graph.mermaid
    assert "write_memories" in graph.mermaid


def test_knowledge_image_retry_job_graph_view_returns_asset_statuses(session):
    space = KnowledgeSpace(name="图片重试")
    session.add(space)
    session.flush()
    assert space.id is not None
    document = KnowledgeDocument(
        space_id=space.id,
        title="lecture",
        source_type="file",
        original_filename="lecture.pdf",
        storage_path=f"files/{space.id}/1/original-lecture.pdf",
        content_hash="hash-lecture",
        parser="pdf",
        status="ready",
        image_asset_failed_count=1,
    )
    session.add(document)
    session.flush()
    assert document.id is not None
    failed_asset = KnowledgeImageAsset(
        space_id=space.id,
        document_id=document.id,
        asset_id="pdf-page-1-image-1",
        asset_uid="failed-asset",
        parser="pdf",
        location_label="PDF 第 1 页图片 1",
        status="failed",
        retryable=True,
        attempt_count=2,
        error_code="DASHSCOPE_REQUEST_TIMEOUT",
        error_message="timeout",
    )
    completed_asset = KnowledgeImageAsset(
        space_id=space.id,
        document_id=document.id,
        asset_id="pdf-page-2-image-1",
        asset_uid="completed-asset",
        parser="pdf",
        location_label="PDF 第 2 页图片 1",
        status="completed",
        attempt_count=1,
    )
    session.add(failed_asset)
    session.add(completed_asset)
    session.flush()
    assert failed_asset.id is not None
    assert completed_asset.id is not None
    job = enqueue_job(
        session,
        job_type=JobType.KNOWLEDGE_IMAGE_RETRY.value,
        graph_name=GraphName.KNOWLEDGE_IMAGE_RETRY.value,
        payload={
            "document_id": document.id,
            "image_asset_ids": [failed_asset.id, completed_asset.id],
        },
    )
    session.commit()
    session.refresh(job)

    graph = get_job_graph(session, job.id or 0)

    assert graph.graph_name == GraphName.KNOWLEDGE_IMAGE_RETRY.value
    assert "load_document" in graph.mermaid
    assert "retry_image_assets" in graph.mermaid
    assert f"asset_{failed_asset.id}" in graph.mermaid
    assert "DASHSCOPE_REQUEST_TIMEOUT" in graph.mermaid
    assert f"class asset_{failed_asset.id} failed;" in graph.mermaid
    assert f"class asset_{completed_asset.id} completed;" in graph.mermaid
