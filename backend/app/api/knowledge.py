import json

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlmodel import Session, select

from app.core.database import get_session
from app.schemas.knowledge import (
    KnowledgeChunkRead,
    KnowledgeChunkDraftRead,
    KnowledgeDocumentRead,
    KnowledgeDocumentRetryResponse,
    KnowledgeDocumentUploadResponse,
    KnowledgeImageAssetRead,
    KnowledgeImageAssetRetryRequest,
    KnowledgeImageAssetRetryResponse,
    KnowledgeOcrInstallRequest,
    KnowledgeOcrInstallResponse,
    KnowledgeOcrStatusRead,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    KnowledgeSearchResultItem,
    KnowledgeSpaceCreate,
    KnowledgeSpaceRead,
    KnowledgeSpaceUpdate,
)
from app.models.knowledge import KnowledgeImageAsset, KnowledgeImageAssetChunk
from app.services.knowledge_document_service import (
    delete_knowledge_document,
    get_knowledge_document,
    preview_document_chunk_drafts,
    list_knowledge_chunks,
    list_knowledge_documents,
    retry_knowledge_document_processing,
    retry_knowledge_document_image_processing,
    upload_knowledge_document,
)
from app.services.knowledge_image_asset_service import (
    active_image_retry_job,
    backfill_document_image_assets,
    enqueue_retry_failed_image_assets,
    enqueue_retry_image_asset,
    list_document_image_assets,
)
from app.services.knowledge_ocr_service import get_knowledge_ocr_status, install_knowledge_ocr
from app.services.knowledge_search_service import KnowledgeSearchResult, search_knowledge
from app.services.knowledge_space_service import (
    archive_knowledge_space,
    create_knowledge_space,
    get_knowledge_space,
    list_knowledge_spaces,
    update_knowledge_space,
)


router = APIRouter(prefix="/knowledge", tags=["knowledge"])


@router.get("/spaces", response_model=list[KnowledgeSpaceRead])
def list_knowledge_spaces_api(
    include_archived: bool = False,
    session: Session = Depends(get_session),
) -> list[KnowledgeSpaceRead]:
    return list_knowledge_spaces(session, include_archived=include_archived)


@router.post("/spaces", response_model=KnowledgeSpaceRead, status_code=status.HTTP_201_CREATED)
def create_knowledge_space_api(
    payload: KnowledgeSpaceCreate,
    session: Session = Depends(get_session),
) -> KnowledgeSpaceRead:
    return create_knowledge_space(session, payload)


@router.get("/spaces/{space_id}", response_model=KnowledgeSpaceRead)
def get_knowledge_space_api(
    space_id: int,
    session: Session = Depends(get_session),
) -> KnowledgeSpaceRead:
    return get_knowledge_space(session, space_id)


@router.patch("/spaces/{space_id}", response_model=KnowledgeSpaceRead)
def update_knowledge_space_api(
    space_id: int,
    payload: KnowledgeSpaceUpdate,
    session: Session = Depends(get_session),
) -> KnowledgeSpaceRead:
    return update_knowledge_space(session, space_id, payload)


@router.delete("/spaces/{space_id}", response_model=KnowledgeSpaceRead)
def archive_knowledge_space_api(
    space_id: int,
    session: Session = Depends(get_session),
) -> KnowledgeSpaceRead:
    return archive_knowledge_space(session, space_id)


@router.post("/search", response_model=KnowledgeSearchResponse)
def search_knowledge_api(
    payload: KnowledgeSearchRequest,
    session: Session = Depends(get_session),
) -> KnowledgeSearchResponse:
    space_ids = [payload.space_id] if payload.space_id is not None else [
        space.id for space in list_knowledge_spaces(session) if space.id is not None
    ]
    result = search_knowledge(
        session,
        query=payload.query,
        space_ids=space_ids,
        top_k=payload.top_k,
        mode=payload.mode,
    )
    return _to_search_response(result)


@router.get("/ocr/status", response_model=KnowledgeOcrStatusRead)
def get_knowledge_ocr_status_api() -> KnowledgeOcrStatusRead:
    return KnowledgeOcrStatusRead(**get_knowledge_ocr_status())


@router.post("/ocr/install", response_model=KnowledgeOcrInstallResponse)
def install_knowledge_ocr_api(payload: KnowledgeOcrInstallRequest) -> KnowledgeOcrInstallResponse:
    try:
        result = install_knowledge_ocr(confirm_install=payload.confirm_install)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return KnowledgeOcrInstallResponse(**result)


@router.get("/spaces/{space_id}/documents", response_model=list[KnowledgeDocumentRead])
def list_knowledge_documents_api(
    space_id: int,
    session: Session = Depends(get_session),
) -> list[KnowledgeDocumentRead]:
    return list_knowledge_documents(session, space_id)


@router.post(
    "/spaces/{space_id}/documents/upload",
    response_model=KnowledgeDocumentUploadResponse,
    status_code=status.HTTP_201_CREATED,
)
async def upload_knowledge_document_api(
    space_id: int,
    file: UploadFile = File(...),
    title: str | None = Form(default=None),
    session: Session = Depends(get_session),
) -> KnowledgeDocumentUploadResponse:
    return await upload_knowledge_document(session, space_id, file, title=title)


@router.get("/documents/{document_id}", response_model=KnowledgeDocumentRead)
def get_knowledge_document_api(
    document_id: int,
    session: Session = Depends(get_session),
) -> KnowledgeDocumentRead:
    return get_knowledge_document(session, document_id)


@router.delete("/documents/{document_id}", response_model=KnowledgeDocumentRead)
def delete_knowledge_document_api(
    document_id: int,
    session: Session = Depends(get_session),
) -> KnowledgeDocumentRead:
    return delete_knowledge_document(session, document_id)


@router.post("/documents/{document_id}/retry-image-processing", response_model=KnowledgeDocumentRetryResponse)
def retry_knowledge_document_image_processing_api(
    document_id: int,
    session: Session = Depends(get_session),
) -> KnowledgeDocumentRetryResponse:
    return retry_knowledge_document_image_processing(session, document_id)


@router.post("/documents/{document_id}/retry-processing", response_model=KnowledgeDocumentRetryResponse)
def retry_knowledge_document_processing_api(
    document_id: int,
    session: Session = Depends(get_session),
) -> KnowledgeDocumentRetryResponse:
    return retry_knowledge_document_processing(session, document_id)


@router.get("/documents/{document_id}/image-assets", response_model=list[KnowledgeImageAssetRead])
def list_knowledge_document_image_assets_api(
    document_id: int,
    session: Session = Depends(get_session),
) -> list[KnowledgeImageAssetRead]:
    get_knowledge_document(session, document_id)
    return [_to_image_asset_read(session, image_asset) for image_asset in list_document_image_assets(session, document_id)]


@router.post(
    "/documents/{document_id}/image-assets/retry-failed",
    response_model=KnowledgeImageAssetRetryResponse,
)
def retry_failed_knowledge_document_image_assets_api(
    document_id: int,
    payload: KnowledgeImageAssetRetryRequest,
    session: Session = Depends(get_session),
) -> KnowledgeImageAssetRetryResponse:
    from app.services.knowledge_document_service import get_document_or_404, to_document_read

    document = get_document_or_404(session, document_id)
    active_job = active_image_retry_job(session, document_id)
    if active_job is not None:
        return KnowledgeImageAssetRetryResponse(document=to_document_read(document), job=_job_payload(active_job), queued_asset_count=0)

    if not list_document_image_assets(session, document_id) and document.image_asset_count > 0:
        backfill_document_image_assets(session, document)
        session.commit()
        session.refresh(document)
    job, queued_count = enqueue_retry_failed_image_assets(
        session,
        document,
        only_retryable=payload.only_retryable,
        max_assets=payload.max_assets,
    )
    session.commit()
    session.refresh(document)
    return KnowledgeImageAssetRetryResponse(
        document=to_document_read(document),
        job=_job_payload(job),
        queued_asset_count=queued_count,
    )


@router.post("/image-assets/{image_asset_id}/retry", response_model=KnowledgeImageAssetRetryResponse)
def retry_knowledge_image_asset_api(
    image_asset_id: int,
    payload: KnowledgeImageAssetRetryRequest,
    session: Session = Depends(get_session),
) -> KnowledgeImageAssetRetryResponse:
    from app.services.knowledge_document_service import to_document_read

    document, job = enqueue_retry_image_asset(session, image_asset_id, force=not payload.only_retryable)
    session.commit()
    session.refresh(document)
    return KnowledgeImageAssetRetryResponse(
        document=to_document_read(document),
        job=_job_payload(job),
        queued_asset_count=1,
    )


@router.get("/documents/{document_id}/chunk-drafts", response_model=list[KnowledgeChunkDraftRead])
def preview_document_chunk_drafts_api(
    document_id: int,
    session: Session = Depends(get_session),
) -> list[KnowledgeChunkDraftRead]:
    return preview_document_chunk_drafts(session, document_id)


@router.get("/documents/{document_id}/chunks", response_model=list[KnowledgeChunkRead])
def list_knowledge_chunks_api(
    document_id: int,
    session: Session = Depends(get_session),
) -> list[KnowledgeChunkRead]:
    return list_knowledge_chunks(session, document_id)


def _to_search_response(result: KnowledgeSearchResult) -> KnowledgeSearchResponse:
    return KnowledgeSearchResponse(
        query=result.query,
        top_k=result.top_k,
        mode=result.mode,
        status=result.status,
        results=[
            KnowledgeSearchResultItem(
                chunk_id=item.chunk_id,
                space_id=item.space_id,
                space_name=item.space_name,
                document_id=item.document_id,
                document_title=item.document_title,
                text=item.text,
                score=item.score,
                score_source=item.score_source,
                heading_path=item.heading_path,
                page_number=item.page_number,
                source_uri=item.source_uri,
                original_filename=item.original_filename,
                retrieval_phase=item.retrieval_phase,
                distance=item.distance,
            )
            for item in result.results
        ],
    )


def _to_image_asset_read(session: Session, image_asset: KnowledgeImageAsset) -> KnowledgeImageAssetRead:
    chunk_ids = [
        link.chunk_id
        for link in session.exec(
            select(KnowledgeImageAssetChunk).where(KnowledgeImageAssetChunk.image_asset_id == (image_asset.id or 0))
        ).all()
    ]
    return KnowledgeImageAssetRead(
        id=image_asset.id or 0,
        space_id=image_asset.space_id,
        document_id=image_asset.document_id,
        asset_id=image_asset.asset_id,
        asset_uid=image_asset.asset_uid,
        parser=image_asset.parser,
        location_label=image_asset.location_label,
        page_number=image_asset.page_number,
        source_offset=image_asset.source_offset,
        heading_path=_decode_heading_path(image_asset.heading_path_json),
        alt_text=image_asset.alt_text,
        caption=image_asset.caption,
        mime_type=image_asset.mime_type,
        width=image_asset.width,
        height=image_asset.height,
        bbox=image_asset.bbox,
        content_hash=image_asset.content_hash,
        byte_size=image_asset.byte_size,
        status=image_asset.status,
        retryable=image_asset.retryable,
        attempt_count=image_asset.attempt_count,
        extractor=image_asset.extractor,
        image_type=image_asset.image_type,
        confidence=image_asset.confidence,
        should_index=image_asset.should_index,
        error_code=image_asset.error_code,
        error_message=image_asset.error_message,
        chunk_ids=chunk_ids,
        last_attempted_at=image_asset.last_attempted_at,
        processed_at=image_asset.processed_at,
        created_at=image_asset.created_at,
        updated_at=image_asset.updated_at,
    )


def _decode_heading_path(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except Exception:
        return []
    if not isinstance(parsed, list):
        return []
    return [str(item) for item in parsed]


def _job_payload(job) -> dict:
    return {
        "id": job.id,
        "type": job.type,
        "graph_name": job.graph_name,
        "status": job.status,
    }
