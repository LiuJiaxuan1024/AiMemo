from __future__ import annotations

import re
from pathlib import Path

from app.rag.document_parsers.base import DocumentBlock, DocumentImageAsset, DocumentParseError, ParsedDocument
from app.rag.document_parsers.text_parser import _decode_text


_HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)]+)\)")


def parse_markdown(path: Path) -> ParsedDocument:
    try:
        text = _decode_text(path.read_bytes())
    except OSError as exc:
        raise DocumentParseError("DOCUMENT_READ_FAILED", f"读取 Markdown 文件失败：{exc}") from exc

    blocks, image_assets = _parse_markdown_blocks(text, path.parent)
    if not blocks:
        if not image_assets:
            raise DocumentParseError("DOCUMENT_TEXT_EMPTY", "未能从 Markdown 文件中提取到内容。")
    title = next((block.text for block in blocks if block.block_type == "heading"), None)
    return ParsedDocument(parser="markdown", blocks=blocks, title=title or path.stem, image_assets=image_assets)


def _parse_markdown_blocks(text: str, base_dir: Path) -> tuple[list[DocumentBlock], list[DocumentImageAsset]]:
    blocks: list[DocumentBlock] = []
    image_assets: list[DocumentImageAsset] = []
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
            image_matches = list(_IMAGE_RE.finditer(stripped)) if not in_code_fence else []
            standalone_images = bool(image_matches) and stripped in {match.group(0) for match in image_matches}
            if standalone_images:
                flush_paragraph()
                for image_index, match in enumerate(image_matches, start=1):
                    image_assets.append(_markdown_image_asset(match, base_dir, heading_stack, offset, image_index))
            else:
                if image_matches:
                    for image_index, match in enumerate(image_matches, start=1):
                        image_assets.append(_markdown_image_asset(match, base_dir, heading_stack, offset, image_index))
                if paragraph_offset is None:
                    paragraph_offset = offset
                paragraph_lines.append(line)
        else:
            flush_paragraph()
        offset += len(raw_line) + 1

    flush_paragraph()
    return blocks, image_assets


def _markdown_image_asset(match: re.Match[str], base_dir: Path, heading_stack: list[str], offset: int, image_index: int) -> DocumentImageAsset:
    target = match.group(2).strip()
    data: bytes | None = None
    mime_type: str | None = None
    if not target.startswith(("http://", "https://", "data:")):
        image_path = (base_dir / target).resolve()
        try:
            data = image_path.read_bytes()
        except OSError:
            data = None
        mime_type = _mime_type_for_path(image_path)
    return DocumentImageAsset(
        asset_id=f"markdown-image-{offset}-{image_index}",
        parser="markdown",
        location_label=f"Markdown 图片 {image_index}",
        heading_path=list(heading_stack),
        source_offset=offset,
        alt_text=match.group(1).strip() or None,
        caption=target,
        data=data,
        mime_type=mime_type,
    )


def _mime_type_for_path(path: Path) -> str | None:
    suffix = path.suffix.lower()
    if suffix in {".jpg", ".jpeg"}:
        return "image/jpeg"
    if suffix == ".png":
        return "image/png"
    if suffix == ".webp":
        return "image/webp"
    if suffix == ".gif":
        return "image/gif"
    return None
