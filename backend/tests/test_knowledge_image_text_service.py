import pytest

from app.core.config import settings
from app.rag.document_parsers.base import DocumentImageAsset
from app.services import knowledge_image_text_service as service
from app.services.knowledge_ocr_service import get_knowledge_ocr_status


def _asset() -> DocumentImageAsset:
    return DocumentImageAsset(
        asset_id="pptx-slide-1-image-1",
        parser="pptx",
        location_label="Slide 1 图片 1",
        heading_path=["Slide 1"],
        page_number=1,
        source_offset=1,
        alt_text="Architecture",
        data=b"fake-image-bytes",
        mime_type="image/png",
        width=800,
        height=600,
    )


def test_qwen_vl_ocr_result_formats_structured_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")
    monkeypatch.setattr(settings, "knowledge_image_text_extraction_provider", "dashscope")
    monkeypatch.setattr(settings, "knowledge_image_text_extraction_model", "qwen-vl-ocr")
    monkeypatch.setattr(settings, "knowledge_image_text_extraction_min_confidence", 0.45)

    def fake_post(payload: dict) -> dict:
        assert payload["model"] == "qwen-vl-ocr"
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"image_type":"diagram","should_index":true,"confidence":0.86,'
                            '"ocr_text":["Client","HMaster"],'
                            '"entities":["HBase","HRegionServer"],'
                            '"summary":"HBase 架构图",'
                            '"key_facts":["Client 通过 Zookeeper 访问 HBase 组件"],'
                            '"warnings":[]}'
                        )
                    }
                }
            ],
            "usage": {"total_tokens": 321},
        }

    monkeypatch.setattr(service, "_post_dashscope_chat_completion", fake_post)

    result = service.extract_qwen_vl_ocr_text(_asset())
    text = service.format_image_text_result(_asset(), result)

    assert result.image_type == "diagram"
    assert result.confidence == 0.86
    assert result.token_usage["total_tokens"] == 321
    assert "提取方式：qwen-vl-ocr" in text
    assert "HBase 架构图" in text
    assert "HRegionServer" in text


def test_qwen_vl_ocr_skips_low_value_images(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")
    monkeypatch.setattr(settings, "knowledge_image_text_extraction_provider", "dashscope")
    monkeypatch.setattr(settings, "knowledge_image_text_extraction_min_confidence", 0.45)

    def fake_post(payload: dict) -> dict:  # noqa: ARG001
        return {
            "choices": [
                {
                    "message": {
                        "content": (
                            '{"image_type":"decorative","should_index":false,"confidence":0.9,'
                            '"ocr_text":[],"entities":[],"summary":"","key_facts":[],"warnings":["decorative"]}'
                        )
                    }
                }
            ]
        }

    monkeypatch.setattr(service, "_post_dashscope_chat_completion", fake_post)

    with pytest.raises(service.ImageTextExtractionError) as exc_info:
        service.extract_qwen_vl_ocr_text(_asset())
    assert exc_info.value.code == "IMAGE_TEXT_SKIPPED_LOW_VALUE"


def test_qwen_vl_ocr_status_uses_dashscope_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "knowledge_image_text_extraction_mode", "qwen_vl_ocr")
    monkeypatch.setattr(settings, "knowledge_image_text_extraction_model", "qwen-vl-ocr")
    monkeypatch.setattr(settings, "dashscope_api_key", "")

    missing = get_knowledge_ocr_status()
    assert missing["status"] == "provider_not_configured"
    assert missing["ready"] is False

    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")
    ready = get_knowledge_ocr_status()
    assert ready["status"] == "ready"
    assert ready["ready"] is True
    assert "qwen-vl-ocr" in ready["message"]
