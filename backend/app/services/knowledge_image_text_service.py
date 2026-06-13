from __future__ import annotations

import base64
from dataclasses import dataclass
import json
import re
import time
from typing import Any
from urllib import error as urllib_error
from urllib import request

from app.core.config import settings
from app.rag.document_parsers.base import DocumentImageAsset


class ImageTextExtractionError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable


@dataclass(frozen=True)
class ImageTextExtractionResult:
    text: str
    extractor: str
    image_type: str
    confidence: float
    token_usage: dict[str, Any]


def extract_qwen_vl_ocr_text(asset: DocumentImageAsset) -> ImageTextExtractionResult:
    _validate_asset(asset)
    payload = _build_dashscope_payload(asset)
    attempts = _configured_max_attempts()
    for attempt in range(1, attempts + 1):
        try:
            response = _post_dashscope_chat_completion(payload)
            content = _extract_message_content(response)
            parsed = _parse_json_object(content)
            result = _build_result_from_json(
                asset,
                parsed,
                usage=response.get("usage") if isinstance(response, dict) else None,
            )
            _validate_result_quality(result, parsed)
            return result
        except ImageTextExtractionError as exc:
            if not exc.retryable or attempt >= attempts:
                raise
            _sleep_before_retry(attempt)
    raise ImageTextExtractionError("IMAGE_TEXT_EXTRACTION_FAILED", "image text extraction failed.")


def format_image_text_result(asset: DocumentImageAsset, result: ImageTextExtractionResult) -> str:
    parts = [
        "[图片文本]",
        f"位置：{asset.location_label}",
        f"资源 ID：{asset.asset_id}",
        f"提取方式：{result.extractor}",
        f"图片类型：{result.image_type or 'unknown'}",
        f"置信度：{result.confidence:.2f}",
    ]
    if asset.alt_text:
        parts.append(f"替代文本：{asset.alt_text}")
    parts.extend(["", result.text.strip()])
    return "\n".join(part for part in parts if part is not None).strip()


def _validate_asset(asset: DocumentImageAsset) -> None:
    if not asset.data:
        raise ImageTextExtractionError("IMAGE_EMPTY", f"image asset {asset.asset_id} has no binary payload.")
    if len(asset.data) > settings.knowledge_image_text_extraction_max_image_bytes:
        raise ImageTextExtractionError(
            "IMAGE_TOO_LARGE",
            (
                f"image asset {asset.asset_id} is too large: "
                f"{len(asset.data)} bytes > {settings.knowledge_image_text_extraction_max_image_bytes} bytes."
            ),
        )
    if not (asset.mime_type or "").startswith("image/"):
        raise ImageTextExtractionError("IMAGE_MIME_UNSUPPORTED", f"unsupported image mime type: {asset.mime_type}")


def _build_dashscope_payload(asset: DocumentImageAsset) -> dict[str, Any]:
    data_url = _image_data_url(asset)
    metadata = {
        "parser": asset.parser,
        "location_label": asset.location_label,
        "asset_id": asset.asset_id,
        "heading_path": asset.heading_path,
        "page_number": asset.page_number,
        "alt_text": asset.alt_text,
        "mime_type": asset.mime_type,
        "width": asset.width,
        "height": asset.height,
    }
    return {
        "model": settings.knowledge_image_text_extraction_model,
        "messages": [
            {
                "role": "system",
                "content": (
                    "你是知识库图片转文本模块。目标不是描述图片好不好看，而是提取可用于检索和问答的事实。"
                    "优先提取图片中真实可见的文字；如果图片是表格、流程图、架构图、坐标图或产品 logo 集合，"
                    "请提取关键实体、关系和图示含义。低价值、低清或无实际信息的图片应设置 should_index=false。"
                    "不确定的文字不要猜。必须只输出 JSON object，不要输出 Markdown。"
                ),
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "请分析这张文档图片，并返回结构化 JSON。\n\n"
                            f"文档上下文：\n{json.dumps(metadata, ensure_ascii=False)}\n\n"
                            "JSON schema：\n"
                            "{"
                            '"image_type":"text|table|chart|diagram|logo_set|screenshot|photo|decorative|unknown",'
                            '"should_index":true,'
                            '"confidence":0.0,'
                            '"ocr_text":["逐行文字"],'
                            '"entities":["关键实体"],'
                            '"summary":"一句话说明图片表达的信息",'
                            '"key_facts":["可用于检索和问答的事实"],'
                            '"warnings":["不确定点或低质量原因"]'
                            "}"
                        ),
                    },
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
        "response_format": {"type": "json_object"},
        "temperature": 0,
    }


def _post_dashscope_chat_completion(payload: dict[str, Any]) -> dict[str, Any]:
    if settings.knowledge_image_text_extraction_provider.strip().lower() != "dashscope":
        raise ImageTextExtractionError("PROVIDER_UNSUPPORTED", "Only DashScope qwen-vl-ocr is supported for now.")
    if not settings.dashscope_api_key:
        raise ImageTextExtractionError("DASHSCOPE_API_KEY_MISSING", "DASHSCOPE_API_KEY is not configured.")
    endpoint = f"{settings.dashscope_base_url.rstrip('/')}/chat/completions"
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        endpoint,
        data=body,
        headers={
            "Authorization": f"Bearer {settings.dashscope_api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=settings.knowledge_image_text_extraction_timeout_seconds) as response:
            raw = response.read()
    except urllib_error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise ImageTextExtractionError(
            "DASHSCOPE_REQUEST_FAILED",
            detail or str(exc),
            retryable=_is_retryable_http_status(getattr(exc, "code", None)),
        ) from exc
    except TimeoutError as exc:
        raise ImageTextExtractionError(
            "DASHSCOPE_REQUEST_TIMEOUT",
            "qwen-vl-ocr request timed out.",
            retryable=True,
        ) from exc
    except OSError as exc:
        raise ImageTextExtractionError("DASHSCOPE_REQUEST_FAILED", str(exc), retryable=True) from exc
    try:
        parsed = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ImageTextExtractionError(
            "DASHSCOPE_BAD_RESPONSE",
            "DashScope response is not valid JSON.",
            retryable=True,
        ) from exc
    if not isinstance(parsed, dict):
        raise ImageTextExtractionError(
            "DASHSCOPE_BAD_RESPONSE",
            "DashScope response is not a JSON object.",
            retryable=True,
        )
    return parsed


def _extract_message_content(response: dict[str, Any]) -> str:
    choices = response.get("choices")
    if not isinstance(choices, list) or not choices:
        raise ImageTextExtractionError(
            "DASHSCOPE_BAD_RESPONSE",
            "DashScope response missing choices.",
            retryable=True,
        )
    message = choices[0].get("message") if isinstance(choices[0], dict) else None
    content = message.get("content") if isinstance(message, dict) else None
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts = [item.get("text", "") for item in content if isinstance(item, dict)]
        return "\n".join(text for text in texts if text)
    raise ImageTextExtractionError(
        "DASHSCOPE_BAD_RESPONSE",
        "DashScope response missing message content.",
        retryable=True,
    )


def _parse_json_object(content: str) -> dict[str, Any]:
    text = content.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    try:
        parsed = json.loads(text)
    except Exception as exc:
        raise ImageTextExtractionError(
            "MODEL_JSON_PARSE_FAILED",
            "qwen-vl-ocr output is not valid JSON.",
            retryable=True,
        ) from exc
    if not isinstance(parsed, dict):
        raise ImageTextExtractionError(
            "MODEL_JSON_PARSE_FAILED",
            "qwen-vl-ocr output is not a JSON object.",
            retryable=True,
        )
    return parsed


def _configured_max_attempts() -> int:
    return max(1, int(settings.knowledge_image_text_extraction_max_attempts or 1))


def _sleep_before_retry(attempt: int) -> None:
    backoff = max(0.0, float(settings.knowledge_image_text_extraction_retry_backoff_seconds or 0.0))
    if backoff <= 0:
        return
    time.sleep(min(backoff * (2 ** max(0, attempt - 1)), 5.0))


def _is_retryable_http_status(status_code: int | None) -> bool:
    return status_code in {408, 409, 425, 429} or (status_code is not None and status_code >= 500)


def _build_result_from_json(asset: DocumentImageAsset, data: dict[str, Any], *, usage: Any) -> ImageTextExtractionResult:
    image_type = str(data.get("image_type") or "unknown").strip() or "unknown"
    confidence = _to_float(data.get("confidence"), default=0.0)
    lines = _string_list(data.get("ocr_text"))
    entities = _string_list(data.get("entities"))
    key_facts = _string_list(data.get("key_facts"))
    summary = str(data.get("summary") or "").strip()

    parts: list[str] = []
    if summary:
        parts.extend(["摘要：", summary])
    if lines:
        parts.extend(["", "可见文字：", *[f"- {line}" for line in lines]])
    if entities:
        parts.extend(["", "关键实体：", *[f"- {entity}" for entity in entities]])
    if key_facts:
        parts.extend(["", "关键事实：", *[f"- {fact}" for fact in key_facts]])
    text = "\n".join(parts).strip()
    return ImageTextExtractionResult(
        text=text,
        extractor=settings.knowledge_image_text_extraction_model or "qwen-vl-ocr",
        image_type=image_type,
        confidence=confidence,
        token_usage=usage if isinstance(usage, dict) else {},
    )


def _validate_result_quality(result: ImageTextExtractionResult, data: dict[str, Any]) -> None:
    if data.get("should_index") is False:
        raise ImageTextExtractionError("IMAGE_TEXT_SKIPPED_LOW_VALUE", "model marked image as not worth indexing.")
    if result.confidence < settings.knowledge_image_text_extraction_min_confidence:
        raise ImageTextExtractionError("IMAGE_TEXT_LOW_CONFIDENCE", f"confidence too low: {result.confidence:.2f}.")
    if not result.text.strip():
        raise ImageTextExtractionError("IMAGE_TEXT_EMPTY", "model returned no indexable image text.")
    if _looks_like_mojibake_or_noise(result.text):
        raise ImageTextExtractionError("IMAGE_TEXT_LOW_QUALITY", "model output looks like mojibake or OCR noise.")


def _looks_like_mojibake_or_noise(text: str) -> bool:
    compact = re.sub(r"\s+", "", text)
    if not compact:
        return True
    mojibake_markers = ("鈥", "�", "鍥", "璁", "鏁", "Ã", "Â")
    if any(marker in compact for marker in mojibake_markers):
        return True
    alnum = sum(1 for char in compact if char.isalnum() or "\u4e00" <= char <= "\u9fff")
    return len(compact) >= 8 and alnum / max(len(compact), 1) < 0.35


def _image_data_url(asset: DocumentImageAsset) -> str:
    mime_type = asset.mime_type or "image/png"
    encoded = base64.b64encode(asset.data or b"").decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def _string_list(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value.strip()] if value.strip() else []
    if not isinstance(value, list):
        return []
    result = []
    for item in value:
        text = str(item).strip()
        if text:
            result.append(text)
    return result


def _to_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
