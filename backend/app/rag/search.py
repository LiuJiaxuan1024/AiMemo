from dataclasses import dataclass
import re

from sqlmodel import Session, col, or_, select

from app.agent.embeddings import embed_texts
from app.core.timing import elapsed_ms, emit_timing, now_counter
from app.models.note import Note
from app.models.note_chunk import NoteChunk
from app.rag.vector_store import search_chunk_embeddings


@dataclass(frozen=True)
class NoteSearchResult:
    """RAG 检索层的业务结果。

    vector_store 只知道 rowid 和 distance；这里补齐 note/chunk 信息，
    让上层 graph 或 API 不需要理解 sqlite-vec 的表结构。
    """

    note_id: int
    note_title: str
    chunk_id: int
    chunk_index: int
    content: str
    content_hash: str
    token_count: int
    distance: float
    score: float


def search_notes(
    session: Session,
    *,
    query: str,
    limit: int = 5,
    embedding_generator=embed_texts,
) -> list[NoteSearchResult]:
    """按语义相似度搜索笔记 chunk。

    这个函数是后续 memory_chat_graph 的检索工具入口。它刻意只返回候选记忆，
    不做 query rewrite、LLM rerank 或回答生成，避免检索层和 Agent 推理耦合。
    """

    normalized_query = query.strip()
    if not normalized_query:
        return []
    if session.exec(select(NoteChunk.id).limit(1)).first() is None:
        return []

    total_started_at = now_counter()
    # 查询文本先向量化，再交给 sqlite-vec 做近邻检索。
    embedding_started_at = now_counter()
    query_embedding = embedding_generator([normalized_query])[0]
    embedding_ms = elapsed_ms(embedding_started_at)
    vector_started_at = now_counter()
    vector_rows = search_chunk_embeddings(query_embedding, limit=limit)
    vector_ms = elapsed_ms(vector_started_at)
    if not vector_rows:
        emit_timing(
            "rag.search_notes_timing",
            total_ms=elapsed_ms(total_started_at),
            embedding_ms=embedding_ms,
            vector_ms=vector_ms,
            chunk_query_ms=0,
            note_query_ms=0,
            assemble_ms=0,
            query_chars=len(normalized_query),
            limit=limit,
            vector_count=0,
            result_count=0,
        )
        return []

    # sqlite-vec 返回的 rowid 就是 notechunk.id。这里保持向量检索顺序，
    # 后续拼接业务表时仍按 distance 从小到大返回。
    chunk_ids = [rowid for rowid, _ in vector_rows]
    distance_by_chunk_id = {rowid: distance for rowid, distance in vector_rows}
    chunk_query_started_at = now_counter()
    chunks = session.exec(select(NoteChunk).where(col(NoteChunk.id).in_(chunk_ids))).all()
    chunk_query_ms = elapsed_ms(chunk_query_started_at)
    chunk_by_id = {chunk.id: chunk for chunk in chunks if chunk.id is not None}

    # 查询 note 标题是为了让前端和 Agent 都能知道命中的记忆来源。
    # 第一版只 join note；未来如果有 longtermmemory，会在更上层做多源 merge。
    note_ids = sorted({chunk.note_id for chunk in chunks})
    note_query_started_at = now_counter()
    notes = session.exec(
        select(Note)
        .where(col(Note.id).in_(note_ids))
        .where(Note.status == "active")
    ).all()
    note_query_ms = elapsed_ms(note_query_started_at)
    note_by_id = {note.id: note for note in notes if note.id is not None}

    assemble_started_at = now_counter()
    results: list[NoteSearchResult] = []
    for chunk_id in chunk_ids:
        chunk = chunk_by_id.get(chunk_id)
        if chunk is None:
            continue
        note = note_by_id.get(chunk.note_id)
        if note is None:
            continue
        distance = distance_by_chunk_id[chunk_id]
        results.append(
            NoteSearchResult(
                note_id=note.id or 0,
                note_title=note.title,
                chunk_id=chunk.id or 0,
                chunk_index=chunk.chunk_index,
                content=chunk.content,
                content_hash=chunk.content_hash,
                token_count=chunk.token_count,
                distance=distance,
                # 对外给一个越高越相关的归一化分数，保留 distance 便于调试。
                score=1 / (1 + max(distance, 0)),
            )
        )
    assemble_ms = elapsed_ms(assemble_started_at)
    emit_timing(
        "rag.search_notes_timing",
        total_ms=elapsed_ms(total_started_at),
        embedding_ms=embedding_ms,
        vector_ms=vector_ms,
        chunk_query_ms=chunk_query_ms,
        note_query_ms=note_query_ms,
        assemble_ms=assemble_ms,
        query_chars=len(normalized_query),
        limit=limit,
        vector_count=len(vector_rows),
        chunk_count=len(chunks),
        note_count=len(notes),
        result_count=len(results),
    )
    return results


def search_notes_keyword(
    session: Session,
    *,
    query: str,
    limit: int = 5,
    candidate_limit: int = 80,
) -> list[NoteSearchResult]:
    """轻量关键词召回个人笔记。

    这是 Memory Chat 每轮可运行的 cheap recall：不请求 embedding，不依赖 sqlite-vec。
    目标不是替代向量检索，而是在绝大多数明显有关键词重叠的个人语境里提供低延迟候选。
    """

    normalized_query = query.strip()
    if not normalized_query:
        return []
    terms = _build_keyword_terms(normalized_query)
    if not terms:
        return []

    predicates = []
    for term in terms[:12]:
        pattern = f"%{term}%"
        predicates.extend(
            [
                col(NoteChunk.content).like(pattern),
                col(Note.title).like(pattern),
                col(Note.summary).like(pattern),
                col(Note.tags).like(pattern),
            ]
        )
    if not predicates:
        return []

    rows = session.exec(
        select(NoteChunk, Note)
        .join(Note, Note.id == NoteChunk.note_id)
        .where(Note.status == "active")
        .where(or_(*predicates))
        .order_by(col(Note.updated_at).desc(), col(NoteChunk.chunk_index).asc())
        .limit(candidate_limit)
    ).all()

    scored: list[tuple[float, NoteChunk, Note]] = []
    seen_chunks: set[int] = set()
    for chunk, note in rows:
        if chunk.id is None or chunk.id in seen_chunks:
            continue
        seen_chunks.add(chunk.id)
        score = _keyword_recall_score(normalized_query, terms, chunk, note)
        if score <= 0:
            continue
        score = max(score, 0.45)
        scored.append((score, chunk, note))

    scored.sort(key=lambda item: (item[0], item[2].updated_at, -(item[1].chunk_index)), reverse=True)
    return [
        NoteSearchResult(
            note_id=note.id or 0,
            note_title=note.title,
            chunk_id=chunk.id or 0,
            chunk_index=chunk.chunk_index,
            content=chunk.content,
            content_hash=chunk.content_hash,
            token_count=chunk.token_count,
            distance=max(0.0, 1.0 - score),
            score=score,
        )
        for score, chunk, note in scored[:limit]
    ]


def _build_keyword_terms(query: str) -> list[str]:
    normalized = query.lower()
    terms: list[str] = []
    terms.extend(re.findall(r"[a-zA-Z0-9_+\-#.]{2,}", normalized))
    cjk_text = "".join(re.findall(r"[\u4e00-\u9fff]+", normalized))
    if len(cjk_text) >= 2:
        terms.extend(_cjk_terms(cjk_text))

    stop_terms = {
        "什么",
        "为什么",
        "怎么",
        "如何",
        "一下",
        "这个",
        "那个",
        "的话",
        "是不是",
        "可以",
        "需要",
    }
    result: list[str] = []
    seen: set[str] = set()
    for term in terms:
        cleaned = term.strip().lower()
        if len(cleaned) < 2 or cleaned in stop_terms or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result


def _cjk_terms(text: str) -> list[str]:
    terms: list[str] = []
    if len(text) >= 3:
        terms.extend(text[index : index + 3] for index in range(0, len(text) - 2))
    terms.extend(text[index : index + 2] for index in range(0, len(text) - 1))
    return terms


def _keyword_recall_score(
    query: str,
    terms: list[str],
    chunk: NoteChunk,
    note: Note,
) -> float:
    title = note.title.lower()
    summary = note.summary.lower()
    tags = note.tags.lower()
    content = chunk.content.lower()
    haystacks = {
        "title": title,
        "tags": tags,
        "summary": summary,
        "content": content,
    }
    score = 0.0
    normalized_query = query.lower()
    if normalized_query and normalized_query in content:
        score += 0.35
    if normalized_query and normalized_query in title:
        score += 0.45

    for term in terms:
        if term in haystacks["title"]:
            score += 0.16
        if term in haystacks["tags"]:
            score += 0.14
        if term in haystacks["summary"]:
            score += 0.1
        if term in haystacks["content"]:
            score += 0.07

    # 关键词召回没有向量相似度那么可靠，压在 0.66 以内，交给 L3 grade 以 weak/good 方式约束使用。
    return min(score, 0.66)
