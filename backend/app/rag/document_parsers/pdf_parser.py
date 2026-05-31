from __future__ import annotations

from pathlib import Path

from app.rag.document_parsers.base import DocumentBlock, DocumentParseError, ParsedDocument


def parse_pdf(path: Path) -> ParsedDocument:
    try:
        import fitz
    except ImportError as exc:
        raise DocumentParseError("DOCUMENT_READER_DEPENDENCY_MISSING", "缺少 PyMuPDF 依赖，无法解析 PDF。") from exc

    try:
        document = fitz.open(str(path))
    except Exception as exc:
        raise DocumentParseError("DOCUMENT_PARSE_FAILED", f"PDF 解析失败：{exc}") from exc

    blocks: list[DocumentBlock] = []
    try:
        for page_index, page in enumerate(document, start=1):
            text = (page.get_text("text") or "").strip()
            if not text:
                continue
            for paragraph in [item.strip() for item in text.split("\n\n") if item.strip()]:
                blocks.append(
                    DocumentBlock(
                        text=paragraph,
                        block_type="paragraph",
                        page_number=page_index,
                    )
                )
    finally:
        document.close()

    if not blocks:
        raise DocumentParseError("DOCUMENT_TEXT_EMPTY", "未能从 PDF 中提取到文本。它可能是扫描件或图片型 PDF。")
    return ParsedDocument(parser="pdf", blocks=blocks, title=path.stem)
