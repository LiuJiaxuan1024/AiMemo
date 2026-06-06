from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from sqlmodel import Session

from app.core.database import get_session
from app.schemas.knowledge import (
    KnowledgeChunkRead,
    KnowledgeChunkDraftRead,
    KnowledgeDocumentRead,
    KnowledgeDocumentUploadResponse,
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
from app.services.knowledge_document_service import (
    delete_knowledge_document,
    get_knowledge_document,
    preview_document_chunk_drafts,
    list_knowledge_chunks,
    list_knowledge_documents,
    upload_knowledge_document,
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
