from __future__ import annotations

from pathlib import Path

from app.rag.document_parsers.base import DocumentParseError, ParsedDocument, paragraph_blocks


def parse_text(path: Path) -> ParsedDocument:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise DocumentParseError("DOCUMENT_READ_FAILED", f"读取文本文件失败：{exc}") from exc

    text = _decode_text(raw)
    blocks = paragraph_blocks(text)
    if not blocks:
        raise DocumentParseError("DOCUMENT_TEXT_EMPTY", "未能从文本文件中提取到内容。")
    return ParsedDocument(parser="text", blocks=blocks, title=path.stem)


def _decode_text(raw: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")
