from dataclasses import dataclass

from app.rag.chunking.config import (
    CHUNK_MAX_TOKENS,
    CHUNK_OVERLAP_TOKENS,
    CHUNK_TARGET_TOKENS,
    SHORT_NOTE_MAX_TOKENS,
)
from app.rag.chunking.tokenizer import count_tokens, decode_tokens, encode_text


@dataclass(frozen=True)
class Chunk:
    index: int
    content: str
    token_count: int


def split_text(text: str) -> list[Chunk]:
    content = text.strip()
    if not content:
        return []

    total_tokens = count_tokens(content)
    if total_tokens <= SHORT_NOTE_MAX_TOKENS:
        return [Chunk(index=0, content=content, token_count=total_tokens)]

    return _split_long_text(content)


def _split_long_text(text: str) -> list[Chunk]:
    # 第一层按段落切，尽量保留语义完整性；如果某个段落过长，再退回 token 硬切。
    paragraphs = [paragraph.strip() for paragraph in text.splitlines() if paragraph.strip()]
    chunks: list[str] = []
    current_parts: list[str] = []
    current_tokens = 0

    for paragraph in paragraphs:
        paragraph_tokens = count_tokens(paragraph)
        if paragraph_tokens > CHUNK_MAX_TOKENS:
            if current_parts:
                chunks.append("\n\n".join(current_parts))
                current_parts = []
                current_tokens = 0
            chunks.extend(_split_by_tokens(paragraph))
            continue

        if current_parts and current_tokens + paragraph_tokens > CHUNK_TARGET_TOKENS:
            chunks.append("\n\n".join(current_parts))
            overlap = _tail_overlap_text(chunks[-1])
            current_parts = [overlap, paragraph] if overlap else [paragraph]
            current_tokens = count_tokens("\n\n".join(current_parts))
        else:
            current_parts.append(paragraph)
            current_tokens += paragraph_tokens

    if current_parts:
        chunks.append("\n\n".join(current_parts))

    normalized_chunks = [chunk.strip() for chunk in chunks if chunk.strip()]
    return [
        Chunk(index=index, content=chunk, token_count=count_tokens(chunk))
        for index, chunk in enumerate(normalized_chunks)
    ]


def _split_by_tokens(text: str) -> list[str]:
    tokens = encode_text(text)
    chunks: list[str] = []
    start = 0
    step = max(1, CHUNK_MAX_TOKENS - CHUNK_OVERLAP_TOKENS)

    while start < len(tokens):
        end = min(start + CHUNK_MAX_TOKENS, len(tokens))
        chunks.append(decode_tokens(tokens[start:end]).strip())
        if end == len(tokens):
            break
        start += step

    return [chunk for chunk in chunks if chunk]


def _tail_overlap_text(text: str) -> str:
    tokens = encode_text(text)
    if len(tokens) <= CHUNK_OVERLAP_TOKENS:
        return text
    return decode_tokens(tokens[-CHUNK_OVERLAP_TOKENS:]).strip()
