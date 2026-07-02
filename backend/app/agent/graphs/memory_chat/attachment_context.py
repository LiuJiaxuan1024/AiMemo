from collections.abc import Callable
from contextlib import AbstractContextManager
import base64
import logging

from langchain_core.messages import HumanMessage
from sqlmodel import Session

from app.agent.context import ContextBudget, ContextLayer
from app.agent.graphs.memory_chat.state import MemoryChatGraphState
from app.agent.model import get_vision_chat_model
from app.core.config import settings
from app.models.chat_attachment import ChatAttachment, ChatAttachmentDerivative
from app.rag.chunking.tokenizer import count_tokens
from app.services.attachment_service import load_attachment_context_for_message, resolve_chat_attachment_path


SessionFactory = Callable[[], AbstractContextManager[Session]]
logger = logging.getLogger(__name__)


def _context_budget() -> ContextBudget:
    return settings.context_pyramid_budget


def _resolve_conversation_id(state: MemoryChatGraphState) -> int:
    conversation_id = state.get("conversation_id")
    if conversation_id is None:
        raise ValueError("conversation_id is required.")
    return int(conversation_id)


def _indent_text(text: str, spaces: int) -> str:
    prefix = " " * spaces
    return "\n".join(f"{prefix}{line}" if line else line for line in text.splitlines())


def _truncate_context_text(text: str, budget_tokens: int) -> str:
    normalized = text.strip()
    if count_tokens(normalized) <= budget_tokens:
        return normalized
    candidate = normalized[: max(1, budget_tokens * 2)]
    while candidate and count_tokens(candidate + "...") > budget_tokens:
        candidate = candidate[: max(1, int(len(candidate) * 0.85))]
    return candidate.rstrip() + "..."




def build_lx_attachment_context_node(session_factory: SessionFactory):
    """构建附件派生上下文层。

    原始附件只作为可回源证据保存；这里默认注入 derivative/metadata 文本。
    """

    def build_lx_attachment_context(state: MemoryChatGraphState) -> MemoryChatGraphState:
        conversation_id = _resolve_conversation_id(state)
        user_message_id = int(state.get("user_message_id") or 0) or None
        attachment_ids = [int(item) for item in state.get("attachment_ids", []) if int(item) > 0]
        with session_factory() as session:
            attachment_context = load_attachment_context_for_message(
                session,
                conversation_id=conversation_id,
                message_id=user_message_id,
                attachment_ids=attachment_ids,
            )
            _ensure_current_turn_image_derivatives(session, attachment_context)
            session.commit()
            attachment_context = load_attachment_context_for_message(
                session,
                conversation_id=conversation_id,
                message_id=user_message_id,
                attachment_ids=attachment_ids,
            )
        if not attachment_context:
            content = "本轮没有可用附件。"
        else:
            sections: list[str] = []
            for attachment, derivatives in attachment_context:
                lines = [
                    f"- attachment_id: {attachment.id}",
                    f"  kind: {attachment.kind}",
                    f"  name: {attachment.original_name}",
                    f"  mime_type: {attachment.mime_type}",
                    f"  size_bytes: {attachment.size_bytes}",
                    f"  storage_path: {attachment.storage_path}",
                    f"  source_hash: {attachment.sha256}",
                ]
                if attachment.width and attachment.height:
                    lines.append(f"  image_dimensions: {attachment.width}x{attachment.height}")
                if derivatives:
                    lines.append("  derived:")
                    for derivative in derivatives:
                        derivative_text = str(derivative.content or "").strip()
                        if derivative_text:
                            lines.append(f"    [{derivative.kind}]\n{_indent_text(derivative_text, 4)}")
                else:
                    lines.append("  derived: 暂无派生文本。")
                lines.append("  fallback: 如果派生信息不足，应根据 attachment_id/storage_path 回源重新解析原始附件。")
                sections.append("\n".join(lines))
            budget_tokens = min(_context_budget().summary_tokens, 4000)
            content = _truncate_context_text("\n\n".join(sections), budget_tokens)
        layer = ContextLayer(
            level=0,
            name="附件派生上下文（Lx）",
            content=content,
            budget_tokens=min(_context_budget().summary_tokens, 4000),
            used_tokens=count_tokens(content),
            note="默认基于派生文本回答；如果派生文本不足，必须回源读取原始附件，不能凭摘要猜测。",
        )
        return {"context_lx_attachment_layer": layer.to_payload()}

    return build_lx_attachment_context




def _ensure_current_turn_image_derivatives(
    session: Session,
    attachment_context: list[tuple[ChatAttachment, list[ChatAttachmentDerivative]]],
) -> None:
    for attachment, derivatives in attachment_context:
        if attachment.kind != "image" or attachment.id is None:
            continue
        has_completed_vision = any(
            derivative.kind == "vision"
            and derivative.status == "completed"
            and derivative.source_hash == attachment.sha256
            for derivative in derivatives
        )
        if has_completed_vision:
            continue
        result = _inspect_image_attachment_payload(
            attachment,
            instruction="请分析这张用户本轮上传的图片，提取主要内容、可见文字、布局和关键细节。",
        )
        status = "completed" if result["ok"] else "failed"
        content = str(result["data"].get("analysis") or result["message"] or "").strip()
        if not content:
            content = "图片视觉解析没有返回有效内容。"
        session.add(
            ChatAttachmentDerivative(
                attachment_id=int(attachment.id),
                kind="vision",
                content=content,
                model=settings.attachments_vision_model,
                prompt_version="auto-current-turn-v1",
                source_hash=attachment.sha256,
                status=status,
            )
        )


def _inspect_image_attachment_payload(attachment: ChatAttachment, *, instruction: str) -> dict:
    try:
        image_path = resolve_chat_attachment_path(attachment.storage_path)
    except ValueError:
        return {
            "ok": False,
            "message": "图片附件文件不存在，无法解析。",
            "error_code": "ATTACHMENT_FILE_NOT_FOUND",
            "data": {"attachment_id": attachment.id},
        }
    if not image_path.exists() or not image_path.is_file():
        return {
            "ok": False,
            "message": "图片附件文件不存在，无法解析。",
            "error_code": "ATTACHMENT_FILE_NOT_FOUND",
            "data": {"attachment_id": attachment.id},
        }
    image_bytes = image_path.read_bytes()
    max_bytes = settings.attachments_image_max_mb * 1024 * 1024
    if len(image_bytes) > max_bytes:
        return {
            "ok": False,
            "message": f"图片超过 {settings.attachments_image_max_mb} MB，无法直接送入视觉模型。",
            "error_code": "IMAGE_TOO_LARGE",
            "data": {
                "attachment_id": attachment.id,
                "size_bytes": len(image_bytes),
            },
        }
    mime_type = attachment.mime_type or "image/png"
    prompt = (
        "你是 AiMemo 的图片解析助手。请基于图片真实视觉内容回答，不要臆测看不见的细节。\n"
        "请提取：主要画面、可见文字/OCR、图表或界面结构、和用户问题相关的关键细节。\n"
        f"用户本次解析要求：{instruction.strip() or '分析图片内容'}\n"
        f"附件信息：attachment_id={attachment.id}, name={attachment.original_name}, "
        f"mime_type={mime_type}, size_bytes={len(image_bytes)}, dimensions={attachment.width}x{attachment.height}。"
    )
    data_url = f"data:{mime_type};base64,{base64.b64encode(image_bytes).decode('ascii')}"
    try:
        from app.agent.graphs.memory_chat import nodes as nodes_facade

        model = nodes_facade.get_vision_chat_model()
        response = model.invoke(
            [
                HumanMessage(
                    content=[
                        {"type": "text", "text": prompt},
                        {"type": "image_url", "image_url": {"url": data_url}},
                    ]
                )
            ]
        )
        analysis = str(response.content or "").strip()
    except Exception as exc:
        logger.exception("inspect_image_attachment_failed attachment_id=%s", attachment.id)
        return {
            "ok": False,
            "message": f"视觉模型解析图片失败：{exc}",
            "error_code": "VISION_MODEL_FAILED",
            "data": {
                "attachment_id": attachment.id,
                "name": attachment.original_name,
                "mime_type": mime_type,
            },
        }
    return {
        "ok": True,
        "message": "图片解析完成。",
        "error_code": "",
        "data": {
            "attachment_id": attachment.id,
            "name": attachment.original_name,
            "mime_type": mime_type,
            "width": attachment.width,
            "height": attachment.height,
            "analysis": analysis,
        },
    }
