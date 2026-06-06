from __future__ import annotations

from pathlib import Path

from app.rag.document_parsers.base import DocumentBlock, DocumentImageAsset, DocumentParseError, ParsedDocument


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
    image_assets: list[DocumentImageAsset] = []
    try:
        for page_index, page in enumerate(document, start=1):
            text = (page.get_text("text") or "").strip()
            if text:
                for paragraph in [item.strip() for item in text.split("\n\n") if item.strip()]:
                    blocks.append(
                        DocumentBlock(
                            text=paragraph,
                            block_type="paragraph",
                            page_number=page_index,
                        )
                    )

            for image_index, image_info in enumerate(page.get_images(full=True), start=1):
                xref = int(image_info[0])
                width = int(image_info[2]) if len(image_info) > 2 and image_info[2] else None
                height = int(image_info[3]) if len(image_info) > 3 and image_info[3] else None
                bbox = _image_bbox(page, xref)
                image_payload = _extract_image_payload(document, xref)
                image_assets.append(
                    DocumentImageAsset(
                        asset_id=f"pdf-page-{page_index}-image-{image_index}",
                        parser="pdf",
                        location_label=f"PDF 第 {page_index} 页图片 {image_index}",
                        page_number=page_index,
                        source_offset=image_index,
                        data=image_payload[0],
                        mime_type=image_payload[1],
                        width=width,
                        height=height,
                        bbox=bbox,
                    )
                )
    finally:
        document.close()

    if not blocks:
        raise DocumentParseError("DOCUMENT_TEXT_EMPTY", "未能从 PDF 中提取到文本。它可能是扫描件或图片型 PDF。")
    return ParsedDocument(parser="pdf", blocks=blocks, title=path.stem, image_assets=image_assets)


def _image_bbox(page, xref: int) -> str | None:
    try:
        rects = page.get_image_rects(xref)
    except Exception:
        return None
    if not rects:
        return None
    rect = rects[0]
    return f"{round(rect.x0, 2)},{round(rect.y0, 2)},{round(rect.x1, 2)},{round(rect.y1, 2)}"


def _extract_image_payload(document, xref: int) -> tuple[bytes | None, str | None]:
    try:
        payload = document.extract_image(xref)
    except Exception:
        return None, None
    data = payload.get("image")
    extension = str(payload.get("ext") or "").lower()
    mime_type = f"image/{'jpeg' if extension == 'jpg' else extension}" if extension else None
    return data if isinstance(data, bytes) else None, mime_type
