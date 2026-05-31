from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone

from sqlmodel import Session, select

from app.agent.embeddings import embed_texts
from app.agent.graphs.note_embedding.state import (
    ChunkPayload,
    NoteEmbeddingGraphState,
    StoredChunkPayload,
)
from app.jobs.payloads import decode_payload
from app.models.job import Job
from app.models.note import Note
from app.models.note_chunk import NoteChunk
from app.rag.chunking import split_text
from app.rag.hashing import content_hash
from app.rag.vector_store import delete_note_chunk_embeddings, upsert_chunk_embeddings


SessionFactory = Callable[[], AbstractContextManager[Session]]
EmbeddingGenerator = Callable[[list[str]], list[list[float]]]


def build_load_note_node(session_factory: SessionFactory):
    def load_note(state: NoteEmbeddingGraphState) -> NoteEmbeddingGraphState:
        note_id = _resolve_note_id(state)
        expected_hash = state.get("content_hash") or ""
        with session_factory() as session:
            note = session.get(Note, note_id)
            if note is None:
                raise ValueError(f"Note {note_id} not found.")
            # 每个 embedding job 只处理创建它时的内容版本；旧 job 遇到新 hash 或
            # deleted note 时必须跳过，避免旧 chunks/vector 重新写入。
            if note.status != "active" or (expected_hash and note.content_hash != expected_hash):
                return {"note_id": note_id, "content_hash": expected_hash, "should_skip": True}
            # embedding graph 独立维护 embedding_status，不复用 metadata 的 processing_status。
            note.embedding_status = "processing"
            note.embedding_error = ""
            note.updated_at = utc_now()
            session.add(note)
            session.commit()
            return {
                "note_id": note_id,
                "content": note.content,
                "content_hash": note.content_hash,
                "should_skip": False,
            }

    return load_note


def build_split_note_node():
    def split_note(state: NoteEmbeddingGraphState) -> NoteEmbeddingGraphState:
        if state.get("should_skip"):
            return {"chunks": []}
        content = state.get("content")
        if not content:
            raise ValueError("Note content is required before chunking.")
        chunks: list[ChunkPayload] = [
            {
                "chunk_index": chunk.index,
                "content": chunk.content,
                "content_hash": content_hash(chunk.content),
                "token_count": chunk.token_count,
            }
            for chunk in split_text(content)
        ]
        return {"chunks": chunks}

    return split_note


def build_write_chunks_node(session_factory: SessionFactory):
    def write_chunks(state: NoteEmbeddingGraphState) -> NoteEmbeddingGraphState:
        if state.get("should_skip"):
            return {"stored_chunks": []}
        note_id = _resolve_note_id(state)
        expected_hash = state.get("content_hash") or ""
        chunks = state.get("chunks", [])
        with session_factory() as session:
            note = session.get(Note, note_id)
            if note is None:
                raise ValueError(f"Note {note_id} not found.")
            if note.status != "active" or (expected_hash and note.content_hash != expected_hash):
                return {"stored_chunks": []}
            # 第一版采用“先清理再重建”，比增量更新更容易验证幂等和恢复行为。
            old_chunks = session.exec(select(NoteChunk).where(NoteChunk.note_id == note_id)).all()
            delete_note_chunk_embeddings([chunk.id for chunk in old_chunks if chunk.id is not None])
            for old_chunk in old_chunks:
                session.delete(old_chunk)
            session.flush()

            stored_chunks: list[StoredChunkPayload] = []
            for chunk in chunks:
                note_chunk = NoteChunk(
                    note_id=note_id,
                    chunk_index=chunk["chunk_index"],
                    content=chunk["content"],
                    content_hash=chunk["content_hash"],
                    token_count=chunk["token_count"],
                    embedding_status="pending",
                )
                session.add(note_chunk)
                session.flush()
                if note_chunk.id is None:
                    raise RuntimeError("Chunk id was not generated.")
                stored_chunks.append(
                    {
                        "id": note_chunk.id,
                        "chunk_index": note_chunk.chunk_index,
                        "content": note_chunk.content,
                        "content_hash": note_chunk.content_hash,
                        "token_count": note_chunk.token_count,
                    }
                )

            session.commit()
            return {"stored_chunks": stored_chunks}

    return write_chunks


def build_generate_embeddings_node(
    embedding_generator: EmbeddingGenerator = embed_texts,
):
    def generate_embeddings(state: NoteEmbeddingGraphState) -> NoteEmbeddingGraphState:
        if state.get("should_skip"):
            return {"embeddings": []}
        stored_chunks = state.get("stored_chunks", [])
        texts = [chunk["content"] for chunk in stored_chunks]
        # embeddings 会进入 checkpoint。若 LLM/网络调用后进程中断，恢复时会继续写向量，
        # 不重复消耗 embedding 请求。
        return {"embeddings": embedding_generator(texts)}

    return generate_embeddings


def build_write_vector_index_node(session_factory: SessionFactory):
    def write_vector_index(state: NoteEmbeddingGraphState) -> NoteEmbeddingGraphState:
        if state.get("should_skip"):
            return {}
        stored_chunks = state.get("stored_chunks", [])
        embeddings = state.get("embeddings", [])
        if len(stored_chunks) != len(embeddings):
            raise ValueError("Stored chunks and embeddings length mismatch.")

        vector_items = [
            (int(chunk["id"]), embedding)
            for chunk, embedding in zip(stored_chunks, embeddings, strict=True)
        ]
        upsert_chunk_embeddings(vector_items)

        with session_factory() as session:
            for chunk in stored_chunks:
                chunk_id = int(chunk["id"])
                note_chunk = session.get(NoteChunk, chunk_id)
                if note_chunk:
                    note_chunk.embedding_status = "completed"
                    note_chunk.embedding_error = ""
                    note_chunk.updated_at = utc_now()
                    session.add(note_chunk)
            session.commit()
        return {}

    return write_vector_index


def build_mark_completed_node(session_factory: SessionFactory):
    def mark_completed(state: NoteEmbeddingGraphState) -> NoteEmbeddingGraphState:
        if state.get("should_skip"):
            return {}
        note_id = _resolve_note_id(state)
        expected_hash = state.get("content_hash") or ""
        with session_factory() as session:
            note = session.get(Note, note_id)
            if note is None:
                raise ValueError(f"Note {note_id} not found.")
            if note.status != "active" or (expected_hash and note.content_hash != expected_hash):
                return {}
            note.embedding_status = "completed"
            note.embedding_error = ""
            note.embedded_at = utc_now()
            note.updated_at = utc_now()
            session.add(note)
            session.commit()
        return {}

    return mark_completed


def build_mark_failed_note(session_factory: SessionFactory):
    def mark_failed(job: Job, error: str) -> None:
        payload = decode_payload(job.payload)
        note_id = int(payload["note_id"])
        with session_factory() as session:
            note = session.get(Note, note_id)
            if note is None:
                return
            note.embedding_status = "failed"
            note.embedding_error = error[:4000]
            note.updated_at = utc_now()
            session.add(note)
            session.commit()

    return mark_failed


def _resolve_note_id(state: NoteEmbeddingGraphState) -> int:
    note_id = state.get("note_id")
    if note_id is None:
        raise ValueError("note_id is required.")
    return int(note_id)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
