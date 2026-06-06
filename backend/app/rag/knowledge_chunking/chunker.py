from __future__ import annotations

from dataclasses import dataclass, field
import json

from app.rag.chunking.tokenizer import count_tokens, decode_tokens, encode_text
from app.rag.document_parsers.base import DocumentBlock
from app.rag.hashing import content_hash
from app.rag.knowledge_chunking.config import (
    KNOWLEDGE_CHUNK_MAX_TOKENS,
    KNOWLEDGE_CHUNK_OVERLAP_TOKENS,
    KNOWLEDGE_CHUNK_TARGET_TOKENS,
)


@dataclass(frozen=True)
class KnowledgeChunkDraft:
    chunk_index: int
    text: str
    heading_path: list[str] = field(default_factory=list)
    page_number: int | None = None
    source_offset: int | None = None
    token_count: int = 0
    content_hash: str = ""
    metadata_json: str | None = None


def build_chunk_drafts(blocks: list[DocumentBlock]) -> list[KnowledgeChunkDraft]:
    normalized_blocks = [block for block in blocks if block.text.strip()]
    if not normalized_blocks:
        return []

    drafts: list[KnowledgeChunkDraft] = []
    current: list[DocumentBlock] = []
    current_tokens = 0

    for block in normalized_blocks:
        if block.block_type == "image":
            if current:
                _append_chunk(drafts, current)
                current = []
                current_tokens = 0
            _append_chunk(drafts, [block])
            continue

        block_tokens = count_tokens(_block_text(block))
        if block_tokens > KNOWLEDGE_CHUNK_MAX_TOKENS:
            if current:
                _append_chunk(drafts, current)
                current = []
                current_tokens = 0
            _append_oversized_block_chunks(drafts, block)
            continue

        starts_new_section = block.block_type == "heading" and current
        exceeds_target = current and current_tokens + block_tokens > KNOWLEDGE_CHUNK_TARGET_TOKENS
        if starts_new_section or exceeds_target:
            _append_chunk(drafts, current)
            current = []
            current_tokens = 0

        current.append(block)
        current_tokens += block_tokens

    if current:
        _append_chunk(drafts, current)

    return drafts


def _append_chunk(drafts: list[KnowledgeChunkDraft], blocks: list[DocumentBlock]) -> None:
    text = "\n\n".join(_block_text(block) for block in blocks if block.text.strip()).strip()
    if not text:
        return
    first = blocks[0]
    metadata = _chunk_metadata(blocks)
    drafts.append(
        KnowledgeChunkDraft(
            chunk_index=len(drafts),
            text=text,
            heading_path=_last_heading_path(blocks),
            page_number=first.page_number,
            source_offset=first.source_offset,
            token_count=count_tokens(text),
            content_hash=content_hash(text),
            metadata_json=json.dumps(metadata, ensure_ascii=False),
        )
    )


def _append_oversized_block_chunks(drafts: list[KnowledgeChunkDraft], block: DocumentBlock) -> None:
    text = _block_text(block)
    tokens = encode_text(text)
    start = 0
    step = max(1, KNOWLEDGE_CHUNK_MAX_TOKENS - KNOWLEDGE_CHUNK_OVERLAP_TOKENS)
    while start < len(tokens):
        end = min(start + KNOWLEDGE_CHUNK_MAX_TOKENS, len(tokens))
        chunk_text = decode_tokens(tokens[start:end]).strip()
        if chunk_text:
            metadata = _chunk_metadata([block])
            metadata["split_from_oversized_block"] = True
            drafts.append(
                KnowledgeChunkDraft(
                    chunk_index=len(drafts),
                    text=chunk_text,
                    heading_path=list(block.heading_path),
                    page_number=block.page_number,
                    source_offset=block.source_offset,
                    token_count=count_tokens(chunk_text),
                    content_hash=content_hash(chunk_text),
                    metadata_json=json.dumps(metadata, ensure_ascii=False),
                )
            )
        if end == len(tokens):
            break
        start += step


def _block_text(block: DocumentBlock) -> str:
    text = block.text.strip()
    if block.block_type == "heading":
        level = int(block.metadata.get("level") or max(1, min(len(block.heading_path), 6)) or 1)
        return f"{'#' * level} {text}"
    return text


def _last_heading_path(blocks: list[DocumentBlock]) -> list[str]:
    for block in reversed(blocks):
        if block.heading_path:
            return list(block.heading_path)
    return []


def _chunk_metadata(blocks: list[DocumentBlock]) -> dict:
    block_types = [block.block_type for block in blocks]
    source_modalities = _dedupe_values(
        str(block.metadata.get("source_modality") or block.block_type) for block in blocks
    )
    asset_ids = _dedupe_values(str(block.metadata.get("asset_id") or "") for block in blocks)
    metadata = {
        "block_types": block_types,
        "source_modalities": source_modalities,
    }
    if asset_ids:
        metadata["asset_ids"] = asset_ids
    if len(blocks) == 1 and blocks[0].metadata:
        metadata["source_metadata"] = dict(blocks[0].metadata)
    return metadata


def _dedupe_values(values) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not value or value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result
