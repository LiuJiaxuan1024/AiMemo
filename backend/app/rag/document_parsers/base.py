from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class DocumentBlock:
    text: str
    block_type: str = "paragraph"
    heading_path: list[str] = field(default_factory=list)
    page_number: int | None = None
    source_offset: int | None = None
    metadata: dict[str, str | int | float | bool | None] = field(default_factory=dict)


@dataclass(frozen=True)
class ParsedDocument:
    parser: str
    blocks: list[DocumentBlock]
    title: str | None = None


class DocumentParseError(Exception):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def normalize_text(text: str) -> str:
    lines = [line.strip() for line in (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    normalized_lines: list[str] = []
    blank_pending = False
    for line in lines:
        if not line:
            blank_pending = bool(normalized_lines)
            continue
        if blank_pending:
            normalized_lines.append("")
            blank_pending = False
        normalized_lines.append(line)
    return "\n".join(normalized_lines).strip()


def paragraph_blocks(text: str) -> list[DocumentBlock]:
    content = normalize_text(text)
    if not content:
        return []
    blocks: list[DocumentBlock] = []
    offset = 0
    for paragraph in content.split("\n\n"):
        item = paragraph.strip()
        if not item:
            offset += len(paragraph) + 2
            continue
        blocks.append(DocumentBlock(text=item, source_offset=offset))
        offset += len(paragraph) + 2
    return blocks
