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
class DocumentImageAsset:
    asset_id: str
    parser: str
    location_label: str
    data: bytes | None = None
    mime_type: str | None = None
    heading_path: list[str] = field(default_factory=list)
    page_number: int | None = None
    source_offset: int | None = None
    alt_text: str | None = None
    caption: str | None = None
    width: int | float | None = None
    height: int | float | None = None
    bbox: str | None = None


@dataclass(frozen=True)
class ParsedDocument:
    parser: str
    blocks: list[DocumentBlock]
    title: str | None = None
    image_assets: list[DocumentImageAsset] = field(default_factory=list)


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


def image_analysis_block(
    *,
    asset: DocumentImageAsset,
    analysis_text: str,
    heading_path: list[str] | None = None,
) -> DocumentBlock:
    """Create a searchable text block from real OCR/vision analysis."""

    text = analysis_text.strip()
    if not text:
        text = "图片视觉分析未返回有效文本。"
    metadata = {
        "source_modality": "image_asset",
        "asset_id": asset.asset_id,
        "parser": asset.parser,
        "location_label": asset.location_label,
        "alt_text": asset.alt_text,
        "caption": asset.caption,
        "width": asset.width,
        "height": asset.height,
        "bounding_box": asset.bbox,
        "analysis_status": "completed",
    }
    return DocumentBlock(
        text=text,
        block_type="image",
        heading_path=list(heading_path or asset.heading_path),
        page_number=asset.page_number,
        source_offset=asset.source_offset,
        metadata={key: value for key, value in metadata.items() if value is not None},
    )
