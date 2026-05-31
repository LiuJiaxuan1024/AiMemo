from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path

from sqlmodel import Session, select

from app.agent.embeddings import embed_texts
from app.agent.graphs.knowledge_ingest.state import (
    KnowledgeChunkPayload,
    KnowledgeIngestGraphState,
    StoredKnowledgeChunkPayload,
)
from app.jobs.payloads import decode_payload
from app.models.job import Job
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument, KnowledgeSpace
from app.models.note import utc_now
from app.rag.document_parsers import parse_document_file
from app.rag.knowledge_chunking import build_chunk_drafts
from app.rag.vector_store import (
    delete_knowledge_chunk_embeddings,
    upsert_knowledge_chunk_embeddings,
)
from app.services import knowledge_document_service


SessionFactory = Callable[[], AbstractContextManager[Session]]
EmbeddingGenerator = Callable[[list[str]], list[list[float]]]


def build_load_document_node(session_factory: SessionFactory):
    def load_document(state: KnowledgeIngestGraphState) -> KnowledgeIngestGraphState:
        document_id = _resolve_document_id(state)
        expected_hash = state.get("content_hash") or ""
        with session_factory() as session:
            document = session.get(KnowledgeDocument, document_id)
            if document is None:
                raise ValueError(f"KnowledgeDocument {document_id} not found.")
            space = session.get(KnowledgeSpace, document.space_id)
            if (
                document.status == "deleted"
                or space is None
                or space.status != "active"
                or (expected_hash and document.content_hash != expected_hash)
            ):
                return {"document_id": document_id, "content_hash": expected_hash, "should_skip": True}
            if not document.storage_path:
                raise ValueError("KnowledgeDocument storage_path is required.")
            path = knowledge_document_service.KNOWLEDGE_DATA_ROOT / document.storage_path
            if not path.exists():
                raise FileNotFoundError(f"Knowledge document file not found: {path}")
            document.status = "parsing"
            document.error_code = None
            document.error_message = None
            document.updated_at = utc_now()
            session.add(document)
            session.commit()
            return {
                "document_id": document_id,
                "space_id": document.space_id,
                "content_hash": document.content_hash,
                "storage_path": document.storage_path,
                "parser": document.parser or "",
                "should_skip": False,
            }

    return load_document


def build_parse_and_chunk_node():
    def parse_and_chunk(state: KnowledgeIngestGraphState) -> KnowledgeIngestGraphState:
        if state.get("should_skip"):
            return {"chunks": []}
        storage_path = state.get("storage_path")
        if not storage_path:
            raise ValueError("storage_path is required before parsing.")
        parsed = parse_document_file(knowledge_document_service.KNOWLEDGE_DATA_ROOT / storage_path)
        drafts = build_chunk_drafts(parsed.blocks)
        chunks: list[KnowledgeChunkPayload] = [
            {
                "chunk_index": draft.chunk_index,
                "text": draft.text,
                "heading_path": draft.heading_path,
                "page_number": draft.page_number,
                "source_offset": draft.source_offset,
                "token_count": draft.token_count,
                "content_hash": draft.content_hash,
                "metadata_json": draft.metadata_json,
            }
            for draft in drafts
        ]
        if not chunks:
            raise ValueError("No chunks generated from knowledge document.")
        return {"parser": parsed.parser, "chunks": chunks}

    return parse_and_chunk


def build_persist_chunks_node(session_factory: SessionFactory):
    def persist_chunks(state: KnowledgeIngestGraphState) -> KnowledgeIngestGraphState:
        if state.get("should_skip"):
            return {"stored_chunks": []}
        document_id = _resolve_document_id(state)
        expected_hash = state.get("content_hash") or ""
        chunks = state.get("chunks", [])
        with session_factory() as session:
            document = session.get(KnowledgeDocument, document_id)
            if document is None:
                raise ValueError(f"KnowledgeDocument {document_id} not found.")
            space = session.get(KnowledgeSpace, document.space_id)
            if (
                document.status == "deleted"
                or space is None
                or space.status != "active"
                or (expected_hash and document.content_hash != expected_hash)
            ):
                return {"stored_chunks": []}
            document.status = "chunking"
            document.parser = state.get("parser") or document.parser
            document.updated_at = utc_now()
            session.add(document)

            old_chunks = session.exec(
                select(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id)
            ).all()
            delete_knowledge_chunk_embeddings([chunk.id for chunk in old_chunks if chunk.id is not None])
            for old_chunk in old_chunks:
                session.delete(old_chunk)
            session.flush()

            stored_chunks: list[StoredKnowledgeChunkPayload] = []
            for chunk in chunks:
                knowledge_chunk = KnowledgeChunk(
                    space_id=document.space_id,
                    document_id=document_id,
                    chunk_index=chunk["chunk_index"],
                    text=chunk["text"],
                    heading_path=_encode_heading_path(chunk.get("heading_path") or []),
                    page_number=chunk.get("page_number"),
                    source_offset=chunk.get("source_offset"),
                    token_count=chunk["token_count"],
                    content_hash=chunk["content_hash"],
                    embedding_status="pending",
                    metadata_json=chunk.get("metadata_json"),
                )
                session.add(knowledge_chunk)
                session.flush()
                if knowledge_chunk.id is None:
                    raise RuntimeError("KnowledgeChunk id was not generated.")
                stored_chunks.append({**chunk, "id": knowledge_chunk.id})

            document.chunk_count = len(stored_chunks)
            document.token_count = sum(int(chunk["token_count"]) for chunk in stored_chunks)
            document.status = "embedding"
            document.updated_at = utc_now()
            session.add(document)
            session.commit()
            return {"stored_chunks": stored_chunks}

    return persist_chunks


def build_generate_embeddings_node(embedding_generator: EmbeddingGenerator = embed_texts):
    def generate_embeddings(state: KnowledgeIngestGraphState) -> KnowledgeIngestGraphState:
        if state.get("should_skip"):
            return {"embeddings": []}
        stored_chunks = state.get("stored_chunks", [])
        return {"embeddings": embedding_generator([chunk["text"] for chunk in stored_chunks])}

    return generate_embeddings


def build_write_vector_index_node(session_factory: SessionFactory):
    def write_vector_index(state: KnowledgeIngestGraphState) -> KnowledgeIngestGraphState:
        if state.get("should_skip"):
            return {}
        stored_chunks = state.get("stored_chunks", [])
        embeddings = state.get("embeddings", [])
        if len(stored_chunks) != len(embeddings):
            raise ValueError("Stored knowledge chunks and embeddings length mismatch.")

        vector_items = [
            (int(chunk["id"]), embedding)
            for chunk, embedding in zip(stored_chunks, embeddings, strict=True)
        ]
        upsert_knowledge_chunk_embeddings(vector_items)

        with session_factory() as session:
            for chunk in stored_chunks:
                chunk_id = int(chunk["id"])
                knowledge_chunk = session.get(KnowledgeChunk, chunk_id)
                if knowledge_chunk:
                    knowledge_chunk.embedding_status = "completed"
                    knowledge_chunk.embedding_error = None
                    knowledge_chunk.updated_at = utc_now()
                    session.add(knowledge_chunk)
            session.commit()
        return {}

    return write_vector_index


def build_mark_ready_node(session_factory: SessionFactory):
    def mark_ready(state: KnowledgeIngestGraphState) -> KnowledgeIngestGraphState:
        if state.get("should_skip"):
            return {}
        document_id = _resolve_document_id(state)
        expected_hash = state.get("content_hash") or ""
        with session_factory() as session:
            document = session.get(KnowledgeDocument, document_id)
            if document is None:
                raise ValueError(f"KnowledgeDocument {document_id} not found.")
            if document.status == "deleted" or (expected_hash and document.content_hash != expected_hash):
                return {}
            document.status = "ready"
            document.error_code = None
            document.error_message = None
            document.processed_at = utc_now()
            document.updated_at = utc_now()
            session.add(document)
            session.commit()
        return {}

    return mark_ready


def build_mark_failed_document(session_factory: SessionFactory):
    def mark_failed(job: Job, error: str) -> None:
        payload = decode_payload(job.payload)
        document_id = int(payload["document_id"])
        with session_factory() as session:
            document = session.get(KnowledgeDocument, document_id)
            if document is None:
                return
            document.status = "failed"
            document.error_code = "KNOWLEDGE_INGEST_FAILED"
            document.error_message = error[:4000]
            document.updated_at = utc_now()
            session.add(document)
            session.commit()

    return mark_failed


def _resolve_document_id(state: KnowledgeIngestGraphState) -> int:
    document_id = state.get("document_id")
    if document_id is None:
        raise ValueError("document_id is required.")
    return int(document_id)


def _encode_heading_path(value: list[str]) -> str | None:
    if not value:
        return None
    import json

    return json.dumps(value, ensure_ascii=False)
