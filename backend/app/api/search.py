from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.core.database import get_session
from app.rag.search import NoteSearchResult as NoteSearchServiceResult
from app.rag.search import search_notes
from app.schemas.search import NoteSearchRequest, NoteSearchResponse, NoteSearchResult


router = APIRouter(prefix="/search", tags=["search"])


@router.post("/notes", response_model=NoteSearchResponse)
def search_notes_api(
    payload: NoteSearchRequest,
    session: Session = Depends(get_session),
) -> NoteSearchResponse:
    # POST 版本为后续复杂检索参数预留，例如 filters、多源检索开关、rerank 参数等。
    results = search_notes(session, query=payload.query, limit=payload.limit)
    return _to_response(query=payload.query, limit=payload.limit, results=results)


@router.get("/notes", response_model=NoteSearchResponse)
def search_notes_query_api(
    q: str = Query(min_length=1),
    limit: int = Query(default=5, ge=1, le=20),
    session: Session = Depends(get_session),
) -> NoteSearchResponse:
    # GET 版本方便浏览器、前端调试和手工验证。
    results = search_notes(session, query=q, limit=limit)
    return _to_response(query=q, limit=limit, results=results)


def _to_response(
    *,
    query: str,
    limit: int,
    results: list[NoteSearchServiceResult],
) -> NoteSearchResponse:
    # API schema 与 service dataclass 分开，避免后续 service 字段调整直接泄漏到 HTTP 契约。
    return NoteSearchResponse(
        query=query,
        limit=limit,
        results=[
            NoteSearchResult(
                note_id=result.note_id,
                note_title=result.note_title,
                chunk_id=result.chunk_id,
                chunk_index=result.chunk_index,
                content=result.content,
                content_hash=result.content_hash,
                token_count=result.token_count,
                distance=result.distance,
                score=result.score,
            )
            for result in results
        ],
    )
