from dataclasses import dataclass

from sqlmodel import Session, col, select

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
