from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
import io
import json
from pathlib import Path
import subprocess
import tempfile

from sqlmodel import Session, select

from app.agent.embeddings import embed_texts
from app.agent.graphs.knowledge_ingest.state import (
    KnowledgeChunkPayload,
    KnowledgeIngestGraphState,
    StoredKnowledgeChunkPayload,
)
from app.core.config import settings
from app.jobs.payloads import decode_payload
from app.models.job import Job
from app.models.knowledge import KnowledgeChunk, KnowledgeDocument, KnowledgeSpace
from app.models.note import utc_now
from app.rag.document_parsers.base import DocumentImageAsset, image_analysis_block
from app.rag.document_parsers import parse_document_file
from app.rag.knowledge_chunking import build_chunk_drafts
from app.rag.vector_store import (
    delete_knowledge_chunk_embeddings,
    upsert_knowledge_chunk_embeddings,
)
from app.services import knowledge_document_service
from app.services.knowledge_image_text_service import (
    extract_qwen_vl_ocr_text,
    format_image_text_result,
)
from app.services.knowledge_ocr_service import resolve_tesseract_runtime


SessionFactory = Callable[[], AbstractContextManager[Session]]
EmbeddingGenerator = Callable[[list[str]], list[list[float]]]
ImageTextExtractor = Callable[[DocumentImageAsset], str]


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


def build_parse_and_chunk_node(image_text_extractor: ImageTextExtractor | None = None):
    def parse_and_chunk(state: KnowledgeIngestGraphState) -> KnowledgeIngestGraphState:
        if state.get("should_skip"):
            return {"chunks": []}
        storage_path = state.get("storage_path")
        if not storage_path:
            raise ValueError("storage_path is required before parsing.")
        parsed = parse_document_file(knowledge_document_service.KNOWLEDGE_DATA_ROOT / storage_path)
        image_blocks, image_failures = _build_image_analysis_blocks(
            parsed.image_assets,
            image_text_extractor or _build_default_image_text_extractor(),
        )
        drafts = build_chunk_drafts([*parsed.blocks, *image_blocks])
        image_asset_count = len(parsed.image_assets)
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
        return {
            "parser": parsed.parser,
            "chunks": chunks,
            "image_asset_count": image_asset_count,
            "image_asset_processed_count": len(image_blocks),
            "image_text_chunk_count": _count_image_chunks(chunks),
            "image_asset_failed_count": image_failures,
        }

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
            document.image_asset_count = int(state.get("image_asset_count") or 0)
            document.image_asset_processed_count = int(state.get("image_asset_processed_count") or 0)
            document.image_text_chunk_count = int(state.get("image_text_chunk_count") or 0)
            document.image_asset_failed_count = int(state.get("image_asset_failed_count") or 0)
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
            document.text_chunk_count = max(0, len(stored_chunks) - document.image_text_chunk_count)
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
    return json.dumps(value, ensure_ascii=False)


def _count_image_chunks(chunks: list[KnowledgeChunkPayload]) -> int:
    count = 0
    for chunk in chunks:
        metadata = _decode_metadata(chunk.get("metadata_json"))
        modalities = metadata.get("source_modalities")
        block_types = metadata.get("block_types")
        if _contains_image_marker(modalities) or _contains_image_marker(block_types):
            count += 1
    return count


def _decode_metadata(value: str | None) -> dict:
    if not value:
        return {}
    try:
        parsed = json.loads(value)
    except Exception:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _contains_image_marker(value) -> bool:
    if isinstance(value, list):
        return any(_contains_image_marker(item) for item in value)
    return isinstance(value, str) and value.startswith("image")


def _build_image_analysis_blocks(
    assets: list[DocumentImageAsset],
    image_text_extractor: ImageTextExtractor,
):
    blocks = []
    failures = 0
    max_images = max(0, int(settings.knowledge_image_text_extraction_max_images_per_document or 0))
    for index, asset in enumerate(assets):
        if max_images and index >= max_images:
            failures += 1
            continue
        try:
            analysis_text = image_text_extractor(asset)
        except Exception:
            failures += 1
            continue
        if not analysis_text.strip():
            failures += 1
            continue
        blocks.append(image_analysis_block(asset=asset, analysis_text=analysis_text))
    return blocks, failures


def _build_default_image_text_extractor() -> ImageTextExtractor:
    mode = settings.knowledge_image_text_extraction_mode.strip().lower()

    def extract(asset: DocumentImageAsset) -> str:
        if mode in {"off", "none", "disabled"}:
            raise ValueError("knowledge image text extraction is disabled.")
        if mode in {"qwen_vl_ocr", "qwen-vl-ocr", "dashscope_qwen_vl_ocr", "auto"}:
            result = extract_qwen_vl_ocr_text(asset)
            return format_image_text_result(asset, result)
        if mode not in {"ocr_first", "local_ocr_first", "ocr_only", "local_ocr_only", "local_ocr"}:
            raise ValueError(f"unsupported knowledge image text extraction mode: {mode}")

        ocr_text = _extract_local_ocr_text(asset)
        if ocr_text:
            return _format_image_extracted_text(asset, method="本地 OCR", text=ocr_text)
        raise ValueError("no image text extracted by local OCR.")

    return extract


def _extract_local_ocr_text(asset: DocumentImageAsset) -> str:
    if not asset.data:
        raise ValueError(f"image asset {asset.asset_id} has no binary payload.")
    languages = settings.knowledge_image_ocr_languages.strip() or "eng"
    timeout = max(1, settings.knowledge_image_ocr_timeout_seconds)
    try:
        return _extract_tesseract_cli_text(asset, languages=languages, timeout=timeout)
    except Exception:
        pass
    try:
        from PIL import Image
        import pytesseract
    except Exception as exc:
        raise ValueError("local OCR requires a Tesseract executable or Pillow + pytesseract.") from exc

    image = Image.open(io.BytesIO(asset.data))
    try:
        return _clean_extracted_text(pytesseract.image_to_string(image, lang=languages, timeout=timeout))
    except Exception:
        if languages == "eng":
            raise
        return _clean_extracted_text(pytesseract.image_to_string(image, lang="eng", timeout=timeout))


def _extract_tesseract_cli_text(asset: DocumentImageAsset, *, languages: str, timeout: int) -> str:
    command, tessdata_dir = resolve_tesseract_runtime(_parse_ocr_languages(languages))
    if not command:
        raise ValueError("tesseract executable is not available.")
    suffix = _image_suffix(asset.mime_type)
    temp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as temp_file:
            temp_file.write(asset.data or b"")
            temp_path = Path(temp_file.name)
        return _run_tesseract(command, temp_path, languages=languages, timeout=timeout, tessdata_dir=tessdata_dir)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def _run_tesseract(command: str, image_path: Path, *, languages: str, timeout: int, tessdata_dir: str | None = None) -> str:
    args = [command, str(image_path), "stdout"]
    if tessdata_dir:
        args.extend(["--tessdata-dir", tessdata_dir])
    args.extend(["-l", languages])
    result = subprocess.run(args, capture_output=True, text=True, timeout=timeout, check=False)
    if result.returncode == 0:
        return _clean_extracted_text(result.stdout)
    if languages != "eng":
        return _run_tesseract(command, image_path, languages="eng", timeout=timeout, tessdata_dir=tessdata_dir)
    raise ValueError(result.stderr.strip() or "tesseract OCR failed.")


def _parse_ocr_languages(value: str) -> list[str]:
    languages = [item.strip() for item in value.replace(",", "+").split("+") if item.strip()]
    return languages or ["eng"]


def _image_suffix(mime_type: str | None) -> str:
    mapping = {
        "image/jpeg": ".jpg",
        "image/png": ".png",
        "image/webp": ".webp",
        "image/gif": ".gif",
        "image/bmp": ".bmp",
        "image/tiff": ".tiff",
    }
    return mapping.get((mime_type or "").lower(), ".img")


def _format_image_extracted_text(asset: DocumentImageAsset, *, method: str, text: str, note: str | None = None) -> str:
    parts = [
        "[图片文本]",
        f"位置：{asset.location_label}",
        f"资源 ID：{asset.asset_id}",
        f"提取方式：{method}",
    ]
    if asset.alt_text:
        parts.append(f"替代文本：{asset.alt_text}")
    if note:
        parts.append(f"说明：{note}")
    parts.extend(["内容：", text.strip()])
    return "\n".join(parts)


def _clean_extracted_text(text: str) -> str:
    lines = [line.strip() for line in text.splitlines()]
    return "\n".join(line for line in lines if line)
