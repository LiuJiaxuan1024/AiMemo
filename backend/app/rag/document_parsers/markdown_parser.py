from __future__ import annotations

import re
from pathlib import Path

from app.rag.document_parsers.base import DocumentBlock, DocumentParseError, ParsedDocument
from app.rag.document_parsers.text_parser import _decode_text


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")


def parse_markdown(path: Path) -> ParsedDocument:
    try:
        text = _decode_text(path.read_bytes())
    except OSError as exc:
        raise DocumentParseError("DOCUMENT_READ_FAILED", f"读取 Markdown 文件失败：{exc}") from exc

    blocks = _parse_markdown_blocks(text)
    if not blocks:
        raise DocumentParseError("DOCUMENT_TEXT_EMPTY", "未能从 Markdown 文件中提取到内容。")
    title = next((block.text for block in blocks if block.block_type == "heading"), None)
    return ParsedDocument(parser="markdown", blocks=blocks, title=title or path.stem)


def _parse_markdown_blocks(text: str) -> list[DocumentBlock]:
    blocks: list[DocumentBlock] = []
    heading_stack: list[str] = []
    paragraph_lines: list[str] = []
    paragraph_offset: int | None = None
    offset = 0
    in_code_fence = False

    def flush_paragraph() -> None:
        nonlocal paragraph_lines, paragraph_offset
        paragraph = "\n".join(line.rstrip() for line in paragraph_lines).strip()
        if paragraph:
            blocks.append(
                DocumentBlock(
                    text=paragraph,
                    block_type="paragraph",
                    heading_path=list(heading_stack),
                    source_offset=paragraph_offset,
                )
            )
        paragraph_lines = []
        paragraph_offset = None

    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            in_code_fence = not in_code_fence
            if paragraph_offset is None:
                paragraph_offset = offset
            paragraph_lines.append(line)
            offset += len(raw_line) + 1
            continue

        heading_match = None if in_code_fence else _HEADING_RE.match(stripped)
        if heading_match:
            flush_paragraph()
            level = len(heading_match.group(1))
            heading = heading_match.group(2).strip()
            heading_stack = heading_stack[: level - 1]
            heading_stack.append(heading)
            blocks.append(
                DocumentBlock(
                    text=heading,
                    block_type="heading",
                    heading_path=list(heading_stack),
                    source_offset=offset,
                    metadata={"level": level},
                )
            )
        elif stripped:
            if paragraph_offset is None:
                paragraph_offset = offset
            paragraph_lines.append(line)
        else:
            flush_paragraph()
        offset += len(raw_line) + 1

    flush_paragraph()
    return blocks
