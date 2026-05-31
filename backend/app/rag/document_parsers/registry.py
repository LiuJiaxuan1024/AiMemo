from __future__ import annotations

from pathlib import Path

from app.rag.document_parsers.base import DocumentParseError, ParsedDocument
from app.rag.document_parsers.docx_parser import parse_docx
from app.rag.document_parsers.markdown_parser import parse_markdown
from app.rag.document_parsers.pdf_parser import parse_pdf
from app.rag.document_parsers.pptx_parser import parse_pptx
from app.rag.document_parsers.text_parser import parse_text


_PARSERS = {
    ".txt": ("text", parse_text),
    ".text": ("text", parse_text),
    ".md": ("markdown", parse_markdown),
    ".markdown": ("markdown", parse_markdown),
    ".docx": ("docx", parse_docx),
    ".pptx": ("pptx", parse_pptx),
    ".pdf": ("pdf", parse_pdf),
}


def supported_document_suffixes() -> set[str]:
    return set(_PARSERS)


def parser_name_for_path(path: Path | str) -> str:
    suffix = Path(path).suffix.lower()
    parser = _PARSERS.get(suffix)
    if parser is None:
        raise DocumentParseError("UNSUPPORTED_DOCUMENT_TYPE", f"暂不支持 {suffix or 'unknown'} 文档类型。")
    return parser[0]


def parse_document_file(path: Path | str) -> ParsedDocument:
    target = Path(path)
    suffix = target.suffix.lower()
    parser = _PARSERS.get(suffix)
    if parser is None:
        raise DocumentParseError("UNSUPPORTED_DOCUMENT_TYPE", f"暂不支持 {suffix or 'unknown'} 文档类型。")
    return parser[1](target)
