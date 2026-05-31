from __future__ import annotations

from pathlib import Path

from app.rag.document_parsers.base import DocumentBlock, DocumentParseError, ParsedDocument


def parse_docx(path: Path) -> ParsedDocument:
    try:
        from docx import Document
    except ImportError as exc:
        raise DocumentParseError("DOCUMENT_READER_DEPENDENCY_MISSING", "缺少 python-docx 依赖，无法解析 DOCX。") from exc

    try:
        document = Document(str(path))
    except Exception as exc:
        raise DocumentParseError("DOCUMENT_PARSE_FAILED", f"DOCX 解析失败：{exc}") from exc

    blocks: list[DocumentBlock] = []
    heading_stack: list[str] = []
    for paragraph in document.paragraphs:
        text = paragraph.text.strip()
        if not text:
            continue
        style_name = (paragraph.style.name if paragraph.style is not None else "").lower()
        if style_name.startswith("heading"):
            level = _heading_level(style_name)
            heading_stack = heading_stack[: level - 1]
            heading_stack.append(text)
            blocks.append(
                DocumentBlock(
                    text=text,
                    block_type="heading",
                    heading_path=list(heading_stack),
                    metadata={"level": level},
                )
            )
        else:
            blocks.append(DocumentBlock(text=text, block_type="paragraph", heading_path=list(heading_stack)))

    for table in document.tables:
        for row in table.rows:
            cells = [cell.text.strip().replace("\n", " ") for cell in row.cells]
            if any(cells):
                blocks.append(DocumentBlock(text=" | ".join(cells), block_type="table", heading_path=list(heading_stack)))

    if not blocks:
        raise DocumentParseError("DOCUMENT_TEXT_EMPTY", "未能从 DOCX 中提取到文本。")
    title = next((block.text for block in blocks if block.block_type == "heading"), None)
    return ParsedDocument(parser="docx", blocks=blocks, title=title or path.stem)


def _heading_level(style_name: str) -> int:
    digits = "".join(char for char in style_name if char.isdigit())
    if not digits:
        return 1
    return max(1, min(int(digits), 6))
