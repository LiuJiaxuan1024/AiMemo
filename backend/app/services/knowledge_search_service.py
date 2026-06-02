from __future__ import annotations

from dataclasses import dataclass, field
import json
from typing import Literal

from sqlmodel import Session, col, select

from app.agent.embeddings import embed_texts
from app.models.knowledge import ConversationKnowledgeMount, KnowledgeChunk, KnowledgeDocument, KnowledgeSpace
from app.rag.vector_store import search_knowledge_chunk_embeddings


KnowledgeSearchMode = Literal["auto", "vector", "keyword", "hybrid"]
NEED_KNOWLEDGE_MOUNT = "NEED_KNOWLEDGE_MOUNT"


@dataclass(frozen=True)
class KnowledgeSearchItem:
    chunk_id: int
    space_id: int
    space_name: str
    document_id: int
    document_title: str
    text: str
    score: float
    score_source: str
    heading_path: list[str]
    page_number: int | None
    source_uri: str | None
    original_filename: str | None
    retrieval_phase: str
    distance: float | None = None


@dataclass(frozen=True)
class KnowledgeSearchResult:
    query: str
    top_k: int
    mode: str
    status: str
    results: list[KnowledgeSearchItem]
    recall_cache: list[KnowledgeSearchItem] = field(default_factory=list)
    per_document_limit: int = 3
    cache_hit: bool = False


def search_knowledge(
    session: Session,
    *,
    query: str,
    space_ids: list[int],
    top_k: int = 8,
    mode: KnowledgeSearchMode = "hybrid",
    per_document_limit: int = 3,
    embedding_generator=embed_texts,
) -> KnowledgeSearchResult:
    normalized_query = query.strip()
    normalized_space_ids = list(dict.fromkeys(int(space_id) for space_id in space_ids if int(space_id) > 0))
    normalized_mode = _normalize_mode(mode)
    normalized_top_k = max(1, min(int(top_k or 8), 20))
    normalized_per_document_limit = max(1, min(int(per_document_limit or 3), 20))
    if not normalized_query or not normalized_space_ids:
        return KnowledgeSearchResult(
            query=normalized_query,
            top_k=normalized_top_k,
            mode=normalized_mode,
            status="ok",
            results=[],
            recall_cache=[],
            per_document_limit=normalized_per_document_limit,
        )

    vector_items: list[KnowledgeSearchItem] = []
    keyword_items: list[KnowledgeSearchItem] = []
    recall_limit = max(normalized_top_k * 3, 12)

    if normalized_mode in {"auto", "vector", "hybrid"}:
        vector_items = _vector_recall(
            session,
            query=normalized_query,
            space_ids=normalized_space_ids,
            limit=recall_limit,
            embedding_generator=embedding_generator,
        )
    if normalized_mode in {"auto", "keyword", "hybrid"}:
        keyword_items = _keyword_recall(
            session,
            query=normalized_query,
            space_ids=normalized_space_ids,
            limit=recall_limit,
        )

    if normalized_mode == "vector":
        recall_cache = vector_items
    elif normalized_mode == "keyword":
        recall_cache = keyword_items
    else:
        recall_cache = _hybrid_rank(vector_items, keyword_items)
    results = select_from_recall_cache(
        recall_cache,
        top_k=normalized_top_k,
        per_document_limit=normalized_per_document_limit,
    )

    return KnowledgeSearchResult(
        query=normalized_query,
        top_k=normalized_top_k,
        mode=normalized_mode,
        status="ok",
        results=results,
        recall_cache=recall_cache,
        per_document_limit=normalized_per_document_limit,
    )


def search_mounted_knowledge(
    session: Session,
    *,
    conversation_id: int,
    query: str,
    top_k: int = 5,
    mode: KnowledgeSearchMode = "hybrid",
    per_document_limit: int = 3,
    embedding_generator=embed_texts,
) -> KnowledgeSearchResult:
    mounts = session.exec(
        select(ConversationKnowledgeMount).where(ConversationKnowledgeMount.conversation_id == conversation_id)
    ).all()
    space_ids = [mount.space_id for mount in mounts]
    if not space_ids:
        return KnowledgeSearchResult(
            query=query.strip(),
            top_k=max(1, min(int(top_k or 5), 20)),
            mode=_normalize_mode(mode),
            status=NEED_KNOWLEDGE_MOUNT,
            results=[],
            recall_cache=[],
            per_document_limit=max(1, min(int(per_document_limit or 3), 20)),
        )
    return search_knowledge(
        session,
        query=query,
        space_ids=space_ids,
        top_k=top_k,
        mode=mode,
        per_document_limit=per_document_limit,
        embedding_generator=embedding_generator,
    )


def _vector_recall(
    session: Session,
    *,
    query: str,
    space_ids: list[int],
    limit: int,
    embedding_generator,
) -> list[KnowledgeSearchItem]:
    query_embedding = embedding_generator([query])[0]
    vector_rows = search_knowledge_chunk_embeddings(query_embedding, limit=limit)
    if not vector_rows:
        return []
    chunk_ids = [rowid for rowid, _ in vector_rows]
    distance_by_chunk_id = {rowid: distance for rowid, distance in vector_rows}
    items = _load_items_for_chunks(
        session,
        chunk_ids=chunk_ids,
        space_ids=space_ids,
        score_by_chunk_id={
            chunk_id: 1 / (1 + max(distance_by_chunk_id[chunk_id], 0))
            for chunk_id in chunk_ids
        },
        score_source="vector",
        retrieval_phase="vector_recall",
        distance_by_chunk_id=distance_by_chunk_id,
    )
    by_id = {item.chunk_id: item for item in items}
    return [by_id[chunk_id] for chunk_id in chunk_ids if chunk_id in by_id]


def _keyword_recall(
    session: Session,
    *,
    query: str,
    space_ids: list[int],
    limit: int,
) -> list[KnowledgeSearchItem]:
    terms = _keyword_terms(query)
    if not terms:
        return []
    statement = _ready_chunk_statement(space_ids)
    for term in terms:
        statement = statement.where(KnowledgeChunk.text.ilike(f"%{term}%"))
    rows = session.exec(statement.limit(limit * 4)).all()
    scored: list[tuple[KnowledgeChunk, KnowledgeDocument, KnowledgeSpace, float]] = []
    for chunk, document, space in rows:
        score = _keyword_score(chunk.text, terms)
        if score <= 0:
            continue
        scored.append((chunk, document, space, score))
    scored.sort(key=lambda row: row[3], reverse=True)
    return [
        _to_item(
            chunk,
            document,
            space,
            score=score,
            score_source="keyword",
            retrieval_phase="keyword_recall",
        )
        for chunk, document, space, score in scored[:limit]
    ]


def _load_items_for_chunks(
    session: Session,
    *,
    chunk_ids: list[int],
    space_ids: list[int],
    score_by_chunk_id: dict[int, float],
    score_source: str,
    retrieval_phase: str,
    distance_by_chunk_id: dict[int, float] | None = None,
) -> list[KnowledgeSearchItem]:
    if not chunk_ids:
        return []
    rows = session.exec(
        _ready_chunk_statement(space_ids).where(col(KnowledgeChunk.id).in_(chunk_ids))
    ).all()
    return [
        _to_item(
            chunk,
            document,
            space,
            score=score_by_chunk_id.get(chunk.id or 0, 0.0),
            score_source=score_source,
            retrieval_phase=retrieval_phase,
            distance=(distance_by_chunk_id or {}).get(chunk.id or 0),
        )
        for chunk, document, space in rows
        if chunk.id is not None
    ]


def _ready_chunk_statement(space_ids: list[int]):
    return (
        select(KnowledgeChunk, KnowledgeDocument, KnowledgeSpace)
        .join(KnowledgeDocument, KnowledgeDocument.id == KnowledgeChunk.document_id)
        .join(KnowledgeSpace, KnowledgeSpace.id == KnowledgeChunk.space_id)
        .where(col(KnowledgeChunk.space_id).in_(space_ids))
        .where(KnowledgeSpace.status == "active")
        .where(KnowledgeDocument.status == "ready")
        .where(KnowledgeChunk.embedding_status == "completed")
    )


def _hybrid_rank(
    vector_items: list[KnowledgeSearchItem],
    keyword_items: list[KnowledgeSearchItem],
) -> list[KnowledgeSearchItem]:
    combined: dict[int, KnowledgeSearchItem] = {}
    scores: dict[int, float] = {}
    sources: dict[int, set[str]] = {}

    for rank, item in enumerate(vector_items, start=1):
        combined[item.chunk_id] = item
        scores[item.chunk_id] = scores.get(item.chunk_id, 0.0) + _rrf(rank)
        sources.setdefault(item.chunk_id, set()).add("vector")
    for rank, item in enumerate(keyword_items, start=1):
        combined.setdefault(item.chunk_id, item)
        scores[item.chunk_id] = scores.get(item.chunk_id, 0.0) + _rrf(rank)
        sources.setdefault(item.chunk_id, set()).add("keyword")

    ranked_ids = sorted(scores, key=lambda chunk_id: scores[chunk_id], reverse=True)
    merged: list[KnowledgeSearchItem] = []
    for chunk_id in ranked_ids:
        item = combined[chunk_id]
        source_set = sources.get(chunk_id, set())
        merged.append(
            KnowledgeSearchItem(
                **{
                    **item.__dict__,
                    "score": scores[chunk_id],
                    "score_source": "hybrid" if len(source_set) > 1 else next(iter(source_set), item.score_source),
                    "retrieval_phase": "hybrid_merge",
                }
            )
        )
    return merged


def select_from_recall_cache(
    items: list[KnowledgeSearchItem],
    *,
    top_k: int,
    per_document_limit: int = 3,
) -> list[KnowledgeSearchItem]:
    return _cap_per_document(items, per_document=per_document_limit)[: max(1, min(int(top_k or 5), 20))]


def _cap_per_document(items: list[KnowledgeSearchItem], *, per_document: int = 3) -> list[KnowledgeSearchItem]:
    counts: dict[int, int] = {}
    capped: list[KnowledgeSearchItem] = []
    for item in items:
        count = counts.get(item.document_id, 0)
        if count >= per_document:
            continue
        counts[item.document_id] = count + 1
        capped.append(item)
    return capped


def _to_item(
    chunk: KnowledgeChunk,
    document: KnowledgeDocument,
    space: KnowledgeSpace,
    *,
    score: float,
    score_source: str,
    retrieval_phase: str,
    distance: float | None = None,
) -> KnowledgeSearchItem:
    return KnowledgeSearchItem(
        chunk_id=chunk.id or 0,
        space_id=space.id or 0,
        space_name=space.name,
        document_id=document.id or 0,
        document_title=document.title,
        text=chunk.text,
        score=score,
        score_source=score_source,
        heading_path=_parse_heading_path(chunk.heading_path),
        page_number=chunk.page_number,
        source_uri=document.source_uri,
        original_filename=document.original_filename,
        retrieval_phase=retrieval_phase,
        distance=distance,
    )


def _parse_heading_path(value: str | None) -> list[str]:
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return []
    return [str(item) for item in parsed] if isinstance(parsed, list) else []


def _keyword_terms(query: str) -> list[str]:
    terms = [term.strip() for term in query.replace("，", " ").replace(",", " ").split() if term.strip()]
    if terms:
        return terms[:5]
    stripped = query.strip()
    return [stripped] if stripped else []


def _keyword_score(text: str, terms: list[str]) -> float:
    lowered = text.lower()
    score = 0.0
    for term in terms:
        score += lowered.count(term.lower())
    return score / max(len(terms), 1)


def _rrf(rank: int, *, k: int = 60) -> float:
    return 1 / (k + rank)


def _normalize_mode(mode: str) -> KnowledgeSearchMode:
    normalized = (mode or "hybrid").strip().lower()
    if normalized == "auto":
        return "hybrid"
    if normalized not in {"vector", "keyword", "hybrid"}:
        return "hybrid"
    return normalized  # type: ignore[return-value]
