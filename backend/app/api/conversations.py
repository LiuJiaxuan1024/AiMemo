from urllib.parse import quote

from fastapi import APIRouter, Depends, Response, status
from sqlmodel import Session

from app.core.database import get_session
from app.schemas.conversation import (
    ChatMessageCreate,
    ChatMessageRead,
    ConversationCreate,
    ConversationExportRequest,
    ConversationListItem,
    ConversationRead,
)
from app.schemas.knowledge import (
    ConversationKnowledgeMountRead,
    ConversationKnowledgeMountReplace,
)
from app.services.conversation_service import (
    append_message,
    create_conversation,
    delete_conversation,
    delete_message_branch,
    get_conversation,
    list_conversations,
    list_messages,
)
from app.services.conversation_export_service import export_conversation_html
from app.services.knowledge_mount_service import (
    add_conversation_knowledge_mount,
    delete_conversation_knowledge_mount,
    list_conversation_knowledge_mounts,
    replace_conversation_knowledge_mounts,
)


router = APIRouter(prefix="/conversations", tags=["conversations"])


@router.post("", response_model=ConversationRead, status_code=status.HTTP_201_CREATED)
def create_conversation_api(
    payload: ConversationCreate,
    session: Session = Depends(get_session),
) -> ConversationRead:
    # 这里只创建业务会话，不启动 LangGraph；graph 会在用户发送 AI 消息时使用同一 thread_id。
    return create_conversation(session, payload)


@router.get("", response_model=list[ConversationListItem])
def list_conversations_api(session: Session = Depends(get_session)) -> list[ConversationListItem]:
    return list_conversations(session)


@router.get("/{conversation_id}", response_model=ConversationRead)
def get_conversation_api(
    conversation_id: int,
    session: Session = Depends(get_session),
) -> ConversationRead:
    return get_conversation(session, conversation_id)


@router.get("/{conversation_id}/messages", response_model=list[ChatMessageRead])
def list_messages_api(
    conversation_id: int,
    session: Session = Depends(get_session),
) -> list[ChatMessageRead]:
    return list_messages(session, conversation_id)


@router.post("/{conversation_id}/export")
def export_conversation_api(
    conversation_id: int,
    payload: ConversationExportRequest,
    session: Session = Depends(get_session),
) -> Response:
    html, filename = export_conversation_html(session, conversation_id, payload)
    ascii_filename = filename.encode("ascii", "ignore").decode("ascii") or "conversation-export.html"
    return Response(
        content=html,
        media_type="text/html; charset=utf-8",
        headers={
            "Content-Disposition": (
                f"attachment; filename=\"{ascii_filename}\"; filename*=UTF-8''{quote(filename)}"
            )
        },
    )


@router.get("/{conversation_id}/knowledge-mounts", response_model=list[ConversationKnowledgeMountRead])
def list_conversation_knowledge_mounts_api(
    conversation_id: int,
    session: Session = Depends(get_session),
) -> list[ConversationKnowledgeMountRead]:
    return list_conversation_knowledge_mounts(session, conversation_id)


@router.put("/{conversation_id}/knowledge-mounts", response_model=list[ConversationKnowledgeMountRead])
def replace_conversation_knowledge_mounts_api(
    conversation_id: int,
    payload: ConversationKnowledgeMountReplace,
    session: Session = Depends(get_session),
) -> list[ConversationKnowledgeMountRead]:
    return replace_conversation_knowledge_mounts(session, conversation_id, payload.space_ids)


@router.post("/{conversation_id}/knowledge-mounts/{space_id}", response_model=ConversationKnowledgeMountRead)
def add_conversation_knowledge_mount_api(
    conversation_id: int,
    space_id: int,
    session: Session = Depends(get_session),
) -> ConversationKnowledgeMountRead:
    return add_conversation_knowledge_mount(session, conversation_id, space_id)


@router.delete("/{conversation_id}/knowledge-mounts/{space_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation_knowledge_mount_api(
    conversation_id: int,
    space_id: int,
    session: Session = Depends(get_session),
) -> None:
    delete_conversation_knowledge_mount(session, conversation_id, space_id)


@router.post(
    "/{conversation_id}/messages",
    response_model=ChatMessageRead,
    status_code=status.HTTP_201_CREATED,
)
def append_message_api(
    conversation_id: int,
    payload: ChatMessageCreate,
    session: Session = Depends(get_session),
) -> ChatMessageRead:
    # MVP 阶段该接口只保存消息，不生成 AI 回复。后续 memory_chat_graph 会复用 service。
    return append_message(session, conversation_id, payload)


@router.delete("/{conversation_id}/messages/{message_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_message_branch_api(
    conversation_id: int,
    message_id: int,
    session: Session = Depends(get_session),
) -> None:
    delete_message_branch(session, conversation_id, message_id)


@router.delete("/{conversation_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_conversation_api(
    conversation_id: int,
    session: Session = Depends(get_session),
) -> None:
    # 级联删除：消息 / 摘要任务 / 后台命令 / 长时记忆 / LangGraph checkpoint 等都会一起释放。
    delete_conversation(session, conversation_id)
