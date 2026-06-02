from fastapi import APIRouter, Depends, File, UploadFile
from fastapi.responses import FileResponse
from sqlmodel import Session

from app.core.database import get_session
from app.schemas.attachment import ChatAttachmentRead
from app.services.attachment_service import (
    create_chat_attachment,
    get_attachment_or_404,
    get_attachment_path_or_404,
)


router = APIRouter(prefix="/conversations", tags=["attachments"])


@router.post("/{conversation_id}/attachments", response_model=ChatAttachmentRead)
async def upload_conversation_attachment_api(
    conversation_id: int,
    file: UploadFile = File(...),
    session: Session = Depends(get_session),
) -> ChatAttachmentRead:
    return await create_chat_attachment(session, conversation_id=conversation_id, upload=file)


@router.get("/{conversation_id}/attachments/{attachment_id}/content")
def get_conversation_attachment_content_api(
    conversation_id: int,
    attachment_id: int,
    session: Session = Depends(get_session),
) -> FileResponse:
    attachment = get_attachment_or_404(
        session,
        conversation_id=conversation_id,
        attachment_id=attachment_id,
    )
    path = get_attachment_path_or_404(
        session,
        conversation_id=conversation_id,
        attachment_id=attachment_id,
    )
    return FileResponse(
        path,
        media_type=attachment.mime_type or None,
        filename=attachment.original_name or path.name,
    )
