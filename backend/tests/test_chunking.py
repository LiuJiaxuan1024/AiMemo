from app.rag.chunking.config import CHUNK_MAX_TOKENS, CHUNK_OVERLAP_TOKENS, SHORT_NOTE_MAX_TOKENS
from app.rag.chunking.splitter import split_text
from app.rag.chunking.tokenizer import count_tokens
from app.rag.hashing import content_hash


def test_short_note_uses_single_chunk():
    text = "今天记录一个短笔记，应该整条进入一个 chunk。"

    chunks = split_text(text)

    assert len(chunks) == 1
    assert chunks[0].content == text
    assert chunks[0].token_count == count_tokens(text)


def test_long_paragraph_falls_back_to_token_chunks_with_overlap():
    text = " ".join(f"token{i}" for i in range(SHORT_NOTE_MAX_TOKENS + 300))

    chunks = split_text(text)

    assert len(chunks) > 1
    assert all(chunk.token_count <= CHUNK_MAX_TOKENS for chunk in chunks)

    first_tokens = set(chunks[0].content.split())
    second_tokens = set(chunks[1].content.split())
    assert len(first_tokens.intersection(second_tokens)) >= CHUNK_OVERLAP_TOKENS // 3


def test_content_hash_is_stable():
    text = "稳定的 chunk 内容"

    assert content_hash(text) == content_hash(text)
    assert content_hash(text) != content_hash(text + " changed")
