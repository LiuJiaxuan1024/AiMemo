from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
import hashlib
import json

from fastapi import HTTPException, status
from sqlmodel import Session, col, desc, func, select

from app.agent.embeddings import embed_texts
from app.core.config import settings
from app.jobs.models import GraphName, JobStatus, JobType
from app.jobs.payloads import decode_payload
from app.jobs.queue import enqueue_job
from app.models.job import Job
from app.models.knowledge import (
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeImageAsset,
    KnowledgeImageAssetChunk,
)
from app.models.note import utc_now
from app.rag.document_parsers.base import DocumentImageAsset
from app.rag.document_parsers import parse_document_file
from app.rag.document_parsers.base import image_analysis_block
from app.rag.knowledge_chunking import build_chunk_drafts
from app.rag.vector_store import delete_knowledge_chunk_embeddings, upsert_knowledge_chunk_embeddings
from app.services.knowledge_image_text_service import (
    ImageTextExtractionError,
    extract_qwen_vl_ocr_text,
    format_image_text_result,
)


SessionFactory = Callable[[], AbstractContextManager[Session]]
ImageTextExtractor = Callable[[DocumentImageAsset], str]

IMAGE_ASSET_COMPLETED = "completed"
IMAGE_ASSET_FAILED = "failed"
IMAGE_ASSET_PENDING = "pending"
IMAGE_ASSET_PROCESSING = "processing"
IMAGE_ASSET_SKIPPED = "skipped"
IMAGE_ASSET_STALE = "stale"

SKIPPED_IMAGE_ERROR_CODES = {
    "IMAGE_TEXT_SKIPPED_LOW_VALUE",
    "IMAGE_TEXT_LOW_CONFIDENCE",
    "IMAGE_TEXT_EMPTY",
    "IMAGE_TEXT_LOW_QUALITY",
    "KNOWLEDGE_IMAGE_TEXT_DISABLED",
    "IMAGE_LIMIT_EXCEEDED",
}


@dataclass(frozen=True)
class ImageAnalysisResult:
    image_asset_id: int
    asset_id: str
    analysis_text: str


def enqueue_retry_failed_image_assets(
    session: Session,
    document: KnowledgeDocument,
    *,
    only_retryable: bool = True,
    max_assets: int = 20,
) -> tuple[Job, int]:
    if document.id is None:
        raise RuntimeError("KnowledgeDocument id is required before retrying image assets.")
    query = select(KnowledgeImageAsset).where(
        KnowledgeImageAsset.document_id == document.id,
        KnowledgeImageAsset.status == IMAGE_ASSET_FAILED,
    )
    if only_retryable:
        query = query.where(KnowledgeImageAsset.retryable == True)  # noqa: E712
    image_assets = session.exec(
        query.order_by(KnowledgeImageAsset.page_number, KnowledgeImageAsset.source_offset, KnowledgeImageAsset.id).limit(max_assets)
    ).all()
    if not image_assets:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "KNOWLEDGE_IMAGE_ASSET_NO_RETRYABLE_FAILURES", "message": "没有可定向重试的失败图片。"},
        )
    image_asset_ids = [asset.id for asset in image_assets if asset.id is not None]
    dedupe_key = f"{JobType.KNOWLEDGE_IMAGE_RETRY.value}:document:{document.id}:assets:{','.join(str(item) for item in image_asset_ids)}"
    job = enqueue_job(
        session,
        job_type=JobType.KNOWLEDGE_IMAGE_RETRY.value,
        graph_name=GraphName.KNOWLEDGE_IMAGE_RETRY.value,
        payload={"document_id": document.id, "image_asset_ids": image_asset_ids},
        dedupe_key=dedupe_key,
    )
    return job, len(image_asset_ids)


def backfill_document_image_assets(session: Session, document: KnowledgeDocument) -> int:
    if document.id is None:
        raise RuntimeError("KnowledgeDocument id is required before backfilling image assets.")
    existing_count = session.exec(
        select(func.count(KnowledgeImageAsset.id)).where(KnowledgeImageAsset.document_id == document.id)
    ).one()
    if int(existing_count or 0) > 0:
        return int(existing_count or 0)
    if not document.storage_path:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "KNOWLEDGE_DOCUMENT_SOURCE_MISSING", "message": "文档缺少原始文件路径，无法补建图片明细。"},
        )

    from app.services import knowledge_document_service

    path = knowledge_document_service.KNOWLEDGE_DATA_ROOT / document.storage_path
    if not path.exists():
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "KNOWLEDGE_DOCUMENT_SOURCE_MISSING", "message": "找不到原始文档文件，无法补建图片明细。"},
        )

    parsed = parse_document_file(path)
    chunk_ids_by_asset_id = _existing_image_chunk_ids_by_asset_id(session, document.id)
    backfilled_count = 0
    for asset in parsed.image_assets:
        asset_uid = build_image_asset_uid(document_id=document.id, asset=asset)
        chunk_ids = chunk_ids_by_asset_id.get(asset.asset_id, [])
        row = KnowledgeImageAsset(
            space_id=document.space_id,
            document_id=document.id,
            asset_id=asset.asset_id,
            asset_uid=asset_uid,
        )
        _update_image_asset_metadata(row, document.space_id, asset)
        if chunk_ids:
            row.status = IMAGE_ASSET_COMPLETED
            row.retryable = False
            row.attempt_count = 1
            row.extractor = settings.knowledge_image_text_extraction_model or None
            row.should_index = True
            row.processed_at = document.processed_at or utc_now()
        elif document.image_asset_failed_count > 0 or document.status == "failed":
            row.status = IMAGE_ASSET_FAILED
            row.retryable = True
            row.error_code = "IMAGE_ASSET_BACKFILLED_UNPROCESSED"
            row.error_message = "旧文档补建图片明细时发现这张图片没有对应的图片 chunk，可定向重试。"
        else:
            row.status = IMAGE_ASSET_SKIPPED
            row.retryable = False
            row.error_code = "IMAGE_ASSET_BACKFILLED_UNPROCESSED"
            row.error_message = "旧文档补建图片明细时未找到这张图片对应的图片 chunk。"
        row.updated_at = utc_now()
        session.add(row)
        session.flush()
        if row.id is not None:
            for chunk_id in chunk_ids:
                session.add(KnowledgeImageAssetChunk(image_asset_id=row.id, chunk_id=chunk_id))
        backfilled_count += 1

    update_document_image_asset_stats(session, document)
    _refresh_document_chunk_counts(session, document)
    session.flush()
    return backfilled_count


def enqueue_retry_image_asset(
    session: Session,
    image_asset_id: int,
    *,
    force: bool = False,
) -> tuple[KnowledgeDocument, Job]:
    image_asset = session.get(KnowledgeImageAsset, image_asset_id)
    if image_asset is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "KNOWLEDGE_IMAGE_ASSET_NOT_FOUND", "message": "图片资源不存在。"},
        )
    document = session.get(KnowledgeDocument, image_asset.document_id)
    if document is None or document.status == "deleted":
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "KNOWLEDGE_DOCUMENT_NOT_FOUND", "message": "Knowledge document not found."},
        )
    if image_asset.status not in {IMAGE_ASSET_FAILED, IMAGE_ASSET_SKIPPED}:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "KNOWLEDGE_IMAGE_ASSET_NOT_RETRYABLE", "message": "只有失败或已跳过的图片可以重试。"},
        )
    if not force and not image_asset.retryable and image_asset.status == IMAGE_ASSET_FAILED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "KNOWLEDGE_IMAGE_ASSET_RETRY_NOT_RECOMMENDED", "message": "这张图片不是可自动重试错误。"},
        )
    dedupe_key = f"{JobType.KNOWLEDGE_IMAGE_RETRY.value}:document:{document.id}:assets:{image_asset.id}"
    job = enqueue_job(
        session,
        job_type=JobType.KNOWLEDGE_IMAGE_RETRY.value,
        graph_name=GraphName.KNOWLEDGE_IMAGE_RETRY.value,
        payload={"document_id": document.id, "image_asset_ids": [image_asset.id]},
        dedupe_key=dedupe_key,
    )
    return document, job


def run_knowledge_image_retry_job(job: Job, *, session_factory: SessionFactory) -> None:
    from app.services import knowledge_document_service

    payload = decode_payload(job.payload)
    document_id = int(payload["document_id"])
    image_asset_ids = [int(item) for item in payload.get("image_asset_ids", [])]
    if not image_asset_ids:
        return

    with session_factory() as session:
        document = session.get(KnowledgeDocument, document_id)
        if document is None or document.status == "deleted":
            return
        if not document.storage_path:
            raise ValueError("Knowledge document has no stored source file.")
        path = knowledge_document_service.KNOWLEDGE_DATA_ROOT / document.storage_path
    parsed = parse_document_file(path)
    asset_payloads = _match_retry_payloads(document_id=document_id, assets=parsed.image_assets)

    for image_asset_id in image_asset_ids:
        with session_factory() as session:
            image_asset = session.get(KnowledgeImageAsset, image_asset_id)
            if image_asset is None or image_asset.document_id != document_id:
                continue
            payload_asset = asset_payloads.get(image_asset.asset_uid) or asset_payloads.get(image_asset.asset_id)
        if payload_asset is None:
            _mark_image_asset_error(
                session_factory,
                image_asset_id,
                code="IMAGE_ASSET_SOURCE_NOT_FOUND",
                message="Cannot find this image asset in the stored source document.",
                retryable=False,
            )
            continue
        _retry_single_image_asset(
            session_factory=session_factory,
            document_id=document_id,
            image_asset_id=image_asset_id,
            asset=payload_asset,
        )

    with session_factory() as session:
        document = session.get(KnowledgeDocument, document_id)
        if document is None:
            return
        update_document_image_asset_stats(session, document)
        _refresh_document_chunk_counts(session, document)
        session.commit()


def process_document_image_assets(
    *,
    session_factory: SessionFactory,
    document: KnowledgeDocument,
    assets: list[DocumentImageAsset],
    image_text_extractor: ImageTextExtractor,
) -> list[ImageAnalysisResult]:
    if document.id is None:
        raise RuntimeError("KnowledgeDocument id is required before processing image assets.")
    _sync_document_image_assets(session_factory=session_factory, document=document, assets=assets)

    results: list[ImageAnalysisResult] = []
    max_images = max(0, int(settings.knowledge_image_text_extraction_max_images_per_document or 0))
    for index, asset in enumerate(assets):
        asset_uid = build_image_asset_uid(document_id=document.id, asset=asset)
        image_asset_id = _image_asset_id_by_uid(session_factory, document.id, asset_uid)
        if image_asset_id is None:
            continue
        if max_images and index >= max_images:
            _mark_image_asset_error(
                session_factory,
                image_asset_id,
                code="IMAGE_LIMIT_EXCEEDED",
                message=f"document image limit exceeded: {max_images}.",
                retryable=False,
            )
            continue
        _mark_image_asset_processing(session_factory, image_asset_id)
        try:
            analysis_text = image_text_extractor(asset)
        except ImageTextExtractionError as exc:
            _mark_image_asset_error(
                session_factory,
                image_asset_id,
                code=exc.code,
                message=exc.message,
                retryable=exc.retryable,
            )
            continue
        except Exception as exc:
            _mark_image_asset_error(
                session_factory,
                image_asset_id,
                code="IMAGE_TEXT_EXTRACTION_FAILED",
                message=str(exc),
                retryable=False,
            )
            continue
        if not analysis_text.strip():
            _mark_image_asset_error(
                session_factory,
                image_asset_id,
                code="IMAGE_TEXT_EMPTY",
                message="image text extractor returned empty text.",
                retryable=False,
            )
            continue
        _mark_image_asset_completed(session_factory, image_asset_id)
        results.append(
            ImageAnalysisResult(
                image_asset_id=image_asset_id,
                asset_id=asset.asset_id,
                analysis_text=analysis_text,
            )
        )
    return results


def replace_document_image_chunk_links(
    session: Session,
    *,
    document_id: int,
    chunks: list[KnowledgeChunk],
) -> None:
    image_assets = session.exec(
        select(KnowledgeImageAsset).where(KnowledgeImageAsset.document_id == document_id)
    ).all()
    if not image_assets:
        return
    image_asset_ids = [asset.id for asset in image_assets if asset.id is not None]
    if image_asset_ids:
        old_links = session.exec(
            select(KnowledgeImageAssetChunk).where(
                col(KnowledgeImageAssetChunk.image_asset_id).in_(image_asset_ids)
            )
        ).all()
        for old_link in old_links:
            session.delete(old_link)

    asset_by_asset_id = {asset.asset_id: asset for asset in image_assets}
    for chunk in chunks:
        if chunk.id is None:
            continue
        for asset_id in _chunk_asset_ids(chunk.metadata_json):
            image_asset = asset_by_asset_id.get(asset_id)
            if image_asset is None or image_asset.id is None:
                continue
            session.add(KnowledgeImageAssetChunk(image_asset_id=image_asset.id, chunk_id=chunk.id))


def image_asset_stats(session: Session, document_id: int) -> dict[str, int]:
    rows = session.exec(
        select(KnowledgeImageAsset.status, func.count(KnowledgeImageAsset.id))
        .where(KnowledgeImageAsset.document_id == document_id)
        .group_by(KnowledgeImageAsset.status)
    ).all()
    counts = {str(status): int(count) for status, count in rows}
    return {
        "image_asset_count": sum(counts.values()),
        "image_asset_processed_count": counts.get(IMAGE_ASSET_COMPLETED, 0) + counts.get(IMAGE_ASSET_SKIPPED, 0),
        "image_asset_failed_count": counts.get(IMAGE_ASSET_FAILED, 0),
    }


def update_document_image_asset_stats(session: Session, document: KnowledgeDocument) -> None:
    if document.id is None:
        return
    stats = image_asset_stats(session, document.id)
    document.image_asset_count = stats["image_asset_count"]
    document.image_asset_processed_count = stats["image_asset_processed_count"]
    document.image_asset_failed_count = stats["image_asset_failed_count"]
    document.updated_at = utc_now()
    session.add(document)


def list_document_image_assets(session: Session, document_id: int) -> list[KnowledgeImageAsset]:
    return session.exec(
        select(KnowledgeImageAsset)
        .where(KnowledgeImageAsset.document_id == document_id)
        .order_by(KnowledgeImageAsset.page_number, KnowledgeImageAsset.source_offset, KnowledgeImageAsset.id)
    ).all()


def active_image_retry_job(session: Session, document_id: int) -> Job | None:
    return session.exec(
        select(Job).where(
            Job.type == JobType.KNOWLEDGE_IMAGE_RETRY.value,
            col(Job.status).in_({JobStatus.PENDING.value, JobStatus.RUNNING.value}),
            Job.dedupe_key.like(f"{JobType.KNOWLEDGE_IMAGE_RETRY.value}:document:{document_id}:%"),
        )
    ).first()


def build_image_asset_uid(*, document_id: int, asset: DocumentImageAsset) -> str:
    content_hash = _image_content_hash(asset.data)
    identity = "|".join(
        [
            str(document_id),
            asset.parser,
            asset.asset_id,
            str(asset.page_number or ""),
            str(asset.source_offset or ""),
            content_hash,
        ]
    )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _retry_single_image_asset(
    *,
    session_factory: SessionFactory,
    document_id: int,
    image_asset_id: int,
    asset: DocumentImageAsset,
) -> None:
    _mark_image_asset_processing(session_factory, image_asset_id)
    try:
        analysis_text = default_image_text_extractor(asset)
    except ImageTextExtractionError as exc:
        _mark_image_asset_error(
            session_factory,
            image_asset_id,
            code=exc.code,
            message=exc.message,
            retryable=exc.retryable,
        )
        return
    except Exception as exc:
        _mark_image_asset_error(
            session_factory,
            image_asset_id,
            code="IMAGE_TEXT_EXTRACTION_FAILED",
            message=str(exc),
            retryable=False,
        )
        return
    if not analysis_text.strip():
        _mark_image_asset_error(
            session_factory,
            image_asset_id,
            code="IMAGE_TEXT_EMPTY",
            message="image text extractor returned empty text.",
            retryable=False,
        )
        return
    with session_factory() as session:
        document = session.get(KnowledgeDocument, document_id)
        image_asset = session.get(KnowledgeImageAsset, image_asset_id)
        if document is None or image_asset is None:
            return
        _delete_existing_image_asset_chunks(session, image_asset)
        draft = build_chunk_drafts([image_analysis_block(asset=asset, analysis_text=analysis_text)])[0]
        next_index = _next_document_chunk_index(session, document_id)
        knowledge_chunk = KnowledgeChunk(
            space_id=document.space_id,
            document_id=document_id,
            chunk_index=next_index,
            text=draft.text,
            heading_path=json.dumps(draft.heading_path, ensure_ascii=False) if draft.heading_path else None,
            page_number=draft.page_number,
            source_offset=draft.source_offset,
            token_count=draft.token_count,
            content_hash=draft.content_hash,
            embedding_status="pending",
            metadata_json=draft.metadata_json,
        )
        session.add(knowledge_chunk)
        session.flush()
        if knowledge_chunk.id is None:
            raise RuntimeError("KnowledgeChunk id was not generated.")
        session.add(KnowledgeImageAssetChunk(image_asset_id=image_asset_id, chunk_id=knowledge_chunk.id))
        session.commit()

        embedding = embed_texts([knowledge_chunk.text])[0]
        upsert_knowledge_chunk_embeddings([(knowledge_chunk.id, embedding)])

        knowledge_chunk.embedding_status = "completed"
        knowledge_chunk.embedding_error = None
        knowledge_chunk.updated_at = utc_now()
        image_asset.status = IMAGE_ASSET_COMPLETED
        image_asset.retryable = False
        image_asset.error_code = None
        image_asset.error_message = None
        image_asset.extractor = settings.knowledge_image_text_extraction_model or None
        image_asset.should_index = True
        image_asset.processed_at = utc_now()
        image_asset.updated_at = utc_now()
        session.add(knowledge_chunk)
        session.add(image_asset)
        update_document_image_asset_stats(session, document)
        _refresh_document_chunk_counts(session, document)
        session.commit()


def default_image_text_extractor(asset: DocumentImageAsset) -> str:
    mode = settings.knowledge_image_text_extraction_mode.strip().lower()
    if mode in {"off", "none", "disabled"}:
        raise ImageTextExtractionError(
            "KNOWLEDGE_IMAGE_TEXT_DISABLED",
            "knowledge image text extraction is disabled.",
            retryable=False,
        )
    if mode in {"qwen_vl_ocr", "qwen-vl-ocr", "dashscope_qwen_vl_ocr", "auto"}:
        result = extract_qwen_vl_ocr_text(asset)
        return format_image_text_result(asset, result)
    raise ImageTextExtractionError(
        "KNOWLEDGE_IMAGE_TEXT_MODE_UNSUPPORTED",
        f"unsupported knowledge image text extraction mode: {mode}",
        retryable=False,
    )


def _match_retry_payloads(*, document_id: int, assets: list[DocumentImageAsset]) -> dict[str, DocumentImageAsset]:
    result: dict[str, DocumentImageAsset] = {}
    for asset in assets:
        result[build_image_asset_uid(document_id=document_id, asset=asset)] = asset
        result[asset.asset_id] = asset
    return result


def _delete_existing_image_asset_chunks(session: Session, image_asset: KnowledgeImageAsset) -> None:
    if image_asset.id is None:
        return
    with session.no_autoflush:
        links = session.exec(
            select(KnowledgeImageAssetChunk).where(KnowledgeImageAssetChunk.image_asset_id == image_asset.id)
        ).all()
        chunk_ids = [link.chunk_id for link in links]
    delete_knowledge_chunk_embeddings(chunk_ids)
    for link in links:
        chunk = session.get(KnowledgeChunk, link.chunk_id)
        session.delete(link)
        if chunk is not None:
            session.delete(chunk)
    session.flush()


def _existing_image_chunk_ids_by_asset_id(session: Session, document_id: int) -> dict[str, list[int]]:
    chunks = session.exec(
        select(KnowledgeChunk).where(KnowledgeChunk.document_id == document_id)
    ).all()
    result: dict[str, list[int]] = {}
    for chunk in chunks:
        if chunk.id is None:
            continue
        for asset_id in _chunk_asset_ids(chunk.metadata_json):
            result.setdefault(asset_id, []).append(chunk.id)
    return result


def _next_document_chunk_index(session: Session, document_id: int) -> int:
    value = session.exec(
        select(KnowledgeChunk.chunk_index)
        .where(KnowledgeChunk.document_id == document_id)
        .order_by(desc(KnowledgeChunk.chunk_index))
    ).first()
    return 0 if value is None else int(value) + 1


def _refresh_document_chunk_counts(session: Session, document: KnowledgeDocument) -> None:
    if document.id is None:
        return
    chunks = session.exec(
        select(KnowledgeChunk).where(KnowledgeChunk.document_id == document.id)
    ).all()
    image_count = sum(1 for chunk in chunks if _chunk_asset_ids(chunk.metadata_json))
    document.chunk_count = len(chunks)
    document.image_text_chunk_count = image_count
    document.text_chunk_count = max(0, document.chunk_count - document.image_text_chunk_count)
    document.token_count = sum(chunk.token_count for chunk in chunks)
    document.updated_at = utc_now()
    session.add(document)


def _sync_document_image_assets(
    *,
    session_factory: SessionFactory,
    document: KnowledgeDocument,
    assets: list[DocumentImageAsset],
) -> None:
    if document.id is None:
        raise RuntimeError("KnowledgeDocument id is required before syncing image assets.")
    with session_factory() as session:
        existing_assets = session.exec(
            select(KnowledgeImageAsset).where(KnowledgeImageAsset.document_id == document.id)
        ).all()
        for image_asset in existing_assets:
            image_asset.status = IMAGE_ASSET_STALE
            image_asset.retryable = False
            image_asset.updated_at = utc_now()
            session.add(image_asset)

        existing_by_uid = {asset.asset_uid: asset for asset in existing_assets}
        for asset in assets:
            asset_uid = build_image_asset_uid(document_id=document.id, asset=asset)
            row = existing_by_uid.get(asset_uid)
            if row is None:
                row = KnowledgeImageAsset(
                    space_id=document.space_id,
                    document_id=document.id,
                    asset_id=asset.asset_id,
                    asset_uid=asset_uid,
                )
            _update_image_asset_metadata(row, document.space_id, asset)
            row.status = IMAGE_ASSET_PENDING
            row.retryable = False
            row.error_code = None
            row.error_message = None
            row.extractor = None
            row.image_type = None
            row.confidence = None
            row.should_index = None
            row.token_usage_json = None
            row.processed_at = None
            row.updated_at = utc_now()
            session.add(row)
        session.commit()


def _update_image_asset_metadata(row: KnowledgeImageAsset, space_id: int, asset: DocumentImageAsset) -> None:
    row.space_id = space_id
    row.asset_id = asset.asset_id
    row.parser = asset.parser
    row.location_label = asset.location_label
    row.page_number = asset.page_number
    row.source_offset = asset.source_offset
    row.heading_path_json = json.dumps(asset.heading_path, ensure_ascii=False) if asset.heading_path else None
    row.alt_text = asset.alt_text
    row.caption = asset.caption
    row.mime_type = asset.mime_type
    row.width = float(asset.width) if asset.width is not None else None
    row.height = float(asset.height) if asset.height is not None else None
    row.bbox = asset.bbox
    row.content_hash = _image_content_hash(asset.data)
    row.byte_size = len(asset.data or b"")


def _image_asset_id_by_uid(session_factory: SessionFactory, document_id: int, asset_uid: str) -> int | None:
    with session_factory() as session:
        image_asset = session.exec(
            select(KnowledgeImageAsset).where(
                KnowledgeImageAsset.document_id == document_id,
                KnowledgeImageAsset.asset_uid == asset_uid,
            )
        ).first()
        return image_asset.id if image_asset else None


def _mark_image_asset_processing(session_factory: SessionFactory, image_asset_id: int) -> None:
    with session_factory() as session:
        image_asset = session.get(KnowledgeImageAsset, image_asset_id)
        if image_asset is None:
            return
        image_asset.status = IMAGE_ASSET_PROCESSING
        image_asset.attempt_count += 1
        image_asset.last_attempted_at = utc_now()
        image_asset.updated_at = utc_now()
        session.add(image_asset)
        session.commit()


def _mark_image_asset_completed(session_factory: SessionFactory, image_asset_id: int) -> None:
    with session_factory() as session:
        image_asset = session.get(KnowledgeImageAsset, image_asset_id)
        if image_asset is None:
            return
        image_asset.status = IMAGE_ASSET_COMPLETED
        image_asset.retryable = False
        image_asset.error_code = None
        image_asset.error_message = None
        image_asset.extractor = settings.knowledge_image_text_extraction_model or None
        image_asset.should_index = True
        image_asset.processed_at = utc_now()
        image_asset.updated_at = utc_now()
        session.add(image_asset)
        session.commit()


def _mark_image_asset_error(
    session_factory: SessionFactory,
    image_asset_id: int,
    *,
    code: str,
    message: str,
    retryable: bool,
) -> None:
    with session_factory() as session:
        image_asset = session.get(KnowledgeImageAsset, image_asset_id)
        if image_asset is None:
            return
        image_asset.status = IMAGE_ASSET_SKIPPED if code in SKIPPED_IMAGE_ERROR_CODES else IMAGE_ASSET_FAILED
        image_asset.retryable = bool(retryable)
        image_asset.error_code = code
        image_asset.error_message = message[:4000]
        image_asset.processed_at = utc_now()
        image_asset.updated_at = utc_now()
        session.add(image_asset)
        session.commit()


def _chunk_asset_ids(metadata_json: str | None) -> list[str]:
    if not metadata_json:
        return []
    try:
        metadata = json.loads(metadata_json)
    except Exception:
        return []
    if not isinstance(metadata, dict):
        return []
    result: list[str] = []
    asset_ids = metadata.get("asset_ids")
    if isinstance(asset_ids, list):
        result.extend(str(asset_id) for asset_id in asset_ids if str(asset_id).strip())
    source_metadata = metadata.get("source_metadata")
    if isinstance(source_metadata, dict) and source_metadata.get("asset_id"):
        result.append(str(source_metadata["asset_id"]))
    seen: set[str] = set()
    deduped: list[str] = []
    for asset_id in result:
        if asset_id in seen:
            continue
        seen.add(asset_id)
        deduped.append(asset_id)
    return deduped


def _image_content_hash(data: bytes | None) -> str:
    if not data:
        return ""
    return hashlib.sha256(data).hexdigest()
