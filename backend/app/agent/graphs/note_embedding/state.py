from typing import TypedDict


class ChunkPayload(TypedDict):
    chunk_index: int
    content: str
    content_hash: str
    token_count: int


class StoredChunkPayload(ChunkPayload):
    id: int


class NoteEmbeddingGraphState(TypedDict, total=False):
    # job_id/thread_id 标识外层任务；note_id 标识 graph 正在处理的笔记。
    job_id: int
    note_id: int
    content: str
    chunks: list[ChunkPayload]
    stored_chunks: list[StoredChunkPayload]
    embeddings: list[list[float]]
