from typing import TypedDict


class KnowledgeChunkPayload(TypedDict, total=False):
    chunk_index: int
    text: str
    heading_path: list[str]
    page_number: int | None
    source_offset: int | None
    token_count: int
    content_hash: str
    metadata_json: str | None


class StoredKnowledgeChunkPayload(KnowledgeChunkPayload):
    id: int


class KnowledgeIngestGraphState(TypedDict, total=False):
    job_id: int
    document_id: int
    content_hash: str
    should_skip: bool
    space_id: int
    storage_path: str
    parser: str
    chunks: list[KnowledgeChunkPayload]
    image_asset_count: int
    image_asset_processed_count: int
    image_text_chunk_count: int
    image_asset_failed_count: int
    stored_chunks: list[StoredKnowledgeChunkPayload]
    embeddings: list[list[float]]
