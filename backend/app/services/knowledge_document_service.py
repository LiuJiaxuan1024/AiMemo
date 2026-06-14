from __future__ import annotations

import hashlib
from pathlib import Path
import re

from fastapi import HTTPException, UploadFile, status
from sqlmodel import Session, col, select

from app.jobs.models import GraphName, JobStatus, JobType
from app.jobs.queue import enqueue_job
from app.models.job import Job
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument, KnowledgeImageAsset, KnowledgeImageAssetChunk
from app.models.note import utc_now
from app.rag.document_parsers import DocumentParseError, parse_document_file, parser_name_for_path, supported_document_suffixes
from app.rag.knowledge_chunking import KnowledgeChunkDraft, build_chunk_drafts
from app.rag.vector_store import delete_knowledge_chunk_embeddings
from app.schemas.knowledge import (
    KnowledgeChunkDraftRead,
    KnowledgeChunkRead,
    KnowledgeDocumentRead,
    KnowledgeDocumentRetryResponse,
    KnowledgeDocumentUploadResponse,
)
from app.services.knowledge_space_service import get_active_space_or_404, get_space_or_404


MAX_KNOWLEDGE_UPLOAD_BYTES = 25 * 1024 * 1024
KNOWLEDGE_DATA_ROOT = Path(__file__).resolve().parents[2] / "data" / "knowledge"
PROCESSING_DOCUMENT_STATUSES = {"pending", "parsing", "chunking", "embedding", "indexing"}


def list_knowledge_documents(session: Session, space_id: int) -> list[KnowledgeDocumentRead]:
    get_space_or_404(session, space_id)
    documents = session.exec(
        select(KnowledgeDocument)
        .where(
            KnowledgeDocument.space_id == space_id,
            KnowledgeDocument.status != "deleted",
        )
        .order_by(KnowledgeDocument.created_at, KnowledgeDocument.id)
    ).all()
    for document in documents:
        _refresh_image_asset_stats_if_present(session, document)
    session.flush()
    return [to_document_read(document) for document in documents]


def get_knowledge_document(session: Session, document_id: int) -> KnowledgeDocumentRead:
    document = get_document_or_404(session, document_id)
    _refresh_image_asset_stats_if_present(session, document)
    session.flush()
    return to_document_read(document)


def delete_knowledge_document(session: Session, document_id: int) -> KnowledgeDocumentRead:
    document = get_document_or_404(session, document_id)
    if document.status in PROCESSING_DOCUMENT_STATUSES:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "KNOWLEDGE_DOCUMENT_ACTIVE", "message": "文档仍在处理中，暂时不能删除。"},
        )

    chunks = session.exec(
        select(KnowledgeChunk).where(KnowledgeChunk.document_id == (document.id or 0))
    ).all()
    image_assets = session.exec(
        select(KnowledgeImageAsset).where(KnowledgeImageAsset.document_id == (document.id or 0))
    ).all()
    image_asset_ids = [asset.id for asset in image_assets if asset.id is not None]
    if image_asset_ids:
        image_asset_links = session.exec(
            select(KnowledgeImageAssetChunk).where(col(KnowledgeImageAssetChunk.image_asset_id).in_(image_asset_ids))
        ).all()
        for image_asset_link in image_asset_links:
            session.delete(image_asset_link)
    delete_knowledge_chunk_embeddings([chunk.id for chunk in chunks if chunk.id is not None])
    for chunk in chunks:
        session.delete(chunk)
    for image_asset in image_assets:
        session.delete(image_asset)

    document.status = "deleted"
    document.error_code = None
    document.error_message = None
    document.chunk_count = 0
    document.text_chunk_count = 0
    document.image_asset_count = 0
    document.image_asset_processed_count = 0
    document.image_text_chunk_count = 0
    document.image_asset_failed_count = 0
    document.image_asset_warning_count = 0
    document.token_count = 0
    document.updated_at = utc_now()
    session.add(document)
    session.commit()
    session.refresh(document)
    _delete_stored_document_file(document)
    return to_document_read(document)


def retry_knowledge_document_image_processing(session: Session, document_id: int) -> KnowledgeDocumentRetryResponse:
    document = get_document_or_404(session, document_id)
    if document.status in PROCESSING_DOCUMENT_STATUSES:
        active_job = _active_knowledge_ingest_job(session, document)
        return KnowledgeDocumentRetryResponse(document=to_document_read(document), job=_job_payload(active_job) if active_job else None)
    if document.status != "failed" and document.image_asset_failed_count <= 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "KNOWLEDGE_DOCUMENT_NO_FAILED_IMAGES", "message": "这个文档没有需要重试的失败图片。"},
        )
    return retry_knowledge_document_processing(session, document_id)


def retry_knowledge_document_processing(session: Session, document_id: int) -> KnowledgeDocumentRetryResponse:
    document = get_document_or_404(session, document_id)
    if document.status in PROCESSING_DOCUMENT_STATUSES:
        active_job = _active_knowledge_ingest_job(session, document)
        return KnowledgeDocumentRetryResponse(document=to_document_read(document), job=_job_payload(active_job) if active_job else None)
    if not document.storage_path:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "DOCUMENT_HAS_NO_STORAGE", "message": "文档没有可重新处理的原始文件。"},
        )
    path = KNOWLEDGE_DATA_ROOT / document.storage_path
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "DOCUMENT_STORAGE_NOT_FOUND", "message": "找不到文档原始文件，无法重试图片处理。"},
        )

    active_job = _active_knowledge_ingest_job(session, document)
    if active_job is not None:
        return KnowledgeDocumentRetryResponse(document=to_document_read(document), job=_job_payload(active_job))

    document.status = "pending"
    document.error_code = None
    document.error_message = None
    document.updated_at = utc_now()
    session.add(document)
    session.flush()
    job = enqueue_knowledge_ingest_job(session, document)
    session.commit()
    session.refresh(document)
    return KnowledgeDocumentRetryResponse(document=to_document_read(document), job=_job_payload(job))


async def upload_knowledge_document(
    session: Session,
    space_id: int,
    file: UploadFile,
    *,
    title: str | None = None,
) -> KnowledgeDocumentUploadResponse:
    get_active_space_or_404(session, space_id)
    original_filename = _normalize_filename(file.filename or "document.txt")
    suffix = Path(original_filename).suffix.lower()
    if suffix not in supported_document_suffixes():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "UNSUPPORTED_DOCUMENT_TYPE", "message": f"暂不支持 {suffix or 'unknown'} 文档类型。"},
        )
    parser = parser_name_for_path(original_filename)

    content = await file.read()
    if not content:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": "DOCUMENT_EMPTY", "message": "上传的文档为空。"},
        )
    if len(content) > MAX_KNOWLEDGE_UPLOAD_BYTES:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"code": "DOCUMENT_TOO_LARGE", "message": "文档超过 25MB 上限。"},
        )

    document = KnowledgeDocument(
        space_id=space_id,
        title=_normalize_title(title or Path(original_filename).stem),
        source_type="file",
        original_filename=original_filename,
        mime_type=file.content_type,
        content_hash=_bytes_hash(content),
        parser=parser,
        chunk_strategy="heading_paragraph_token",
        status="pending",
    )
    session.add(document)
    session.flush()
    if document.id is None:
        raise RuntimeError("KnowledgeDocument id was not generated before saving file.")

    storage_path = _document_storage_path(space_id, document.id, original_filename)
    absolute_path = KNOWLEDGE_DATA_ROOT / storage_path
    absolute_path.parent.mkdir(parents=True, exist_ok=True)
    absolute_path.write_bytes(content)
    document.storage_path = storage_path.as_posix()
    document.updated_at = utc_now()
    session.add(document)
    session.commit()
    session.refresh(document)
    job = enqueue_knowledge_ingest_job(session, document)
    session.commit()
    session.refresh(document)
    return KnowledgeDocumentUploadResponse(document=to_document_read(document), job=_job_payload(job))


def enqueue_knowledge_ingest_job(session: Session, document: KnowledgeDocument) -> Job:
    if document.id is None:
        raise RuntimeError("KnowledgeDocument id is required before enqueueing ingest job.")
    return enqueue_job(
        session,
        job_type=JobType.KNOWLEDGE_INGEST.value,
        graph_name=GraphName.KNOWLEDGE_INGEST.value,
        payload={"document_id": document.id, "content_hash": document.content_hash},
        dedupe_key=_knowledge_ingest_dedupe_key(document),
    )


def build_document_chunk_drafts(document_path: Path) -> list[KnowledgeChunkDraft]:
    parsed = parse_document_file(document_path)
    return build_chunk_drafts(parsed.blocks)


def preview_document_chunk_drafts(session: Session, document_id: int) -> list[KnowledgeChunkDraftRead]:
    document = get_document_or_404(session, document_id)
    if not document.storage_path:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "DOCUMENT_HAS_NO_STORAGE", "message": "Document has no stored file."},
        )
    path = KNOWLEDGE_DATA_ROOT / document.storage_path
    try:
        drafts = build_document_chunk_drafts(path)
    except DocumentParseError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail={"code": exc.code, "message": exc.message},
        ) from exc
    return [to_chunk_draft_read(draft) for draft in drafts]


def list_knowledge_chunks(session: Session, document_id: int) -> list[KnowledgeChunkRead]:
    document = get_document_or_404(session, document_id)
    chunks = session.exec(
        select(KnowledgeChunk)
        .where(KnowledgeChunk.document_id == (document.id or 0))
        .order_by(KnowledgeChunk.chunk_index, KnowledgeChunk.id)
    ).all()
    return [to_chunk_read(chunk) for chunk in chunks]


def get_document_or_404(session: Session, document_id: int) -> KnowledgeDocument:
    document = session.get(KnowledgeDocument, document_id)
    if document is None or document.status == "deleted":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "KNOWLEDGE_DOCUMENT_NOT_FOUND", "message": "Knowledge document not found."},
        )
    return document


def to_document_read(document: KnowledgeDocument) -> KnowledgeDocumentRead:
    return KnowledgeDocumentRead(
        id=document.id or 0,
        space_id=document.space_id,
        title=document.title,
        source_type=document.source_type,
        source_uri=document.source_uri,
        storage_path=document.storage_path,
        original_filename=document.original_filename,
        mime_type=document.mime_type,
        content_hash=document.content_hash,
        parser=document.parser,
        chunk_strategy=document.chunk_strategy,
        status=document.status,
        chunk_count=document.chunk_count,
        text_chunk_count=document.text_chunk_count,
        image_asset_count=document.image_asset_count,
        image_asset_processed_count=document.image_asset_processed_count,
        image_text_chunk_count=document.image_text_chunk_count,
        image_asset_failed_count=document.image_asset_failed_count,
        image_asset_warning_count=document.image_asset_warning_count,
        token_count=document.token_count,
        error_code=document.error_code,
        error_message=document.error_message,
        created_at=document.created_at,
        updated_at=document.updated_at,
        processed_at=document.processed_at,
    )


def to_chunk_draft_read(draft: KnowledgeChunkDraft) -> KnowledgeChunkDraftRead:
    return KnowledgeChunkDraftRead(
        chunk_index=draft.chunk_index,
        text=draft.text,
        heading_path=draft.heading_path,
        page_number=draft.page_number,
        source_offset=draft.source_offset,
        token_count=draft.token_count,
        content_hash=draft.content_hash,
        metadata_json=draft.metadata_json,
    )


def to_chunk_read(chunk: KnowledgeChunk) -> KnowledgeChunkRead:
    return KnowledgeChunkRead(
        id=chunk.id or 0,
        space_id=chunk.space_id,
        document_id=chunk.document_id,
        chunk_index=chunk.chunk_index,
        text=chunk.text,
        summary=chunk.summary,
        heading_path=chunk.heading_path,
        page_number=chunk.page_number,
        source_offset=chunk.source_offset,
        token_count=chunk.token_count,
        content_hash=chunk.content_hash,
        embedding_status=chunk.embedding_status,
        embedding_error=chunk.embedding_error,
        metadata_json=chunk.metadata_json,
        created_at=chunk.created_at,
        updated_at=chunk.updated_at,
    )


def _refresh_image_asset_stats_if_present(session: Session, document: KnowledgeDocument) -> None:
    if document.id is None or document.image_asset_count <= 0:
        return
    from app.services.knowledge_image_asset_service import update_document_image_asset_stats

    update_document_image_asset_stats(session, document)


def _document_storage_path(space_id: int, document_id: int, filename: str) -> Path:
    suffix = Path(filename).suffix.lower() or ".txt"
    stem = _safe_filename_stem(Path(filename).stem)
    return Path("files") / str(space_id) / str(document_id) / f"original-{stem}{suffix}"


def _normalize_filename(filename: str) -> str:
    name = Path(filename.replace("\\", "/")).name.strip()
    return name[:240] or "document.txt"


def _safe_filename_stem(stem: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", stem.strip()).strip("-._")
    return (safe or "document")[:80]


def _normalize_title(value: str) -> str:
    title = value.strip()
    if not title:
        title = "未命名文档"
    return title[:240]


def _bytes_hash(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _knowledge_ingest_dedupe_key(document: KnowledgeDocument) -> str:
    return f"{JobType.KNOWLEDGE_INGEST.value}:document:{document.id}:content:{document.content_hash}"


def _active_knowledge_ingest_job(session: Session, document: KnowledgeDocument) -> Job | None:
    return session.exec(
        select(Job).where(
            Job.dedupe_key == _knowledge_ingest_dedupe_key(document),
            col(Job.status).in_({JobStatus.PENDING.value, JobStatus.RUNNING.value}),
        )
    ).first()


def _job_payload(job: Job) -> dict:
    return {
        "id": job.id,
        "type": job.type,
        "graph_name": job.graph_name,
        "status": job.status,
    }


def _delete_stored_document_file(document: KnowledgeDocument) -> None:
    if not document.storage_path:
        return
    root = KNOWLEDGE_DATA_ROOT.resolve()
    path = (KNOWLEDGE_DATA_ROOT / document.storage_path).resolve()
    try:
        path.relative_to(root)
    except ValueError:
        return
    try:
        path.unlink(missing_ok=True)
        parent = path.parent
        if parent != root and parent.exists() and not any(parent.iterdir()):
            parent.rmdir()
    except OSError:
        return
