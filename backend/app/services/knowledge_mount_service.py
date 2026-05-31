from __future__ import annotations

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.models.conversation import Conversation
from app.models.knowledge import ConversationKnowledgeMount, KnowledgeDocument, KnowledgeSpace
from app.schemas.knowledge import ConversationKnowledgeMountRead
from app.services.knowledge_space_service import get_active_space_or_404


def list_conversation_knowledge_mounts(
    session: Session,
    conversation_id: int,
) -> list[ConversationKnowledgeMountRead]:
    _get_conversation_or_404(session, conversation_id)
    mounts = session.exec(
        select(ConversationKnowledgeMount)
        .where(ConversationKnowledgeMount.conversation_id == conversation_id)
        .order_by(ConversationKnowledgeMount.created_at, ConversationKnowledgeMount.id)
    ).all()
    return [to_mount_read(session, mount) for mount in mounts]


def replace_conversation_knowledge_mounts(
    session: Session,
    conversation_id: int,
    space_ids: list[int],
) -> list[ConversationKnowledgeMountRead]:
    _get_conversation_or_404(session, conversation_id)
    normalized_space_ids = list(dict.fromkeys(space_ids))
    for space_id in normalized_space_ids:
        get_active_space_or_404(session, space_id)

    existing_mounts = session.exec(
        select(ConversationKnowledgeMount).where(
            ConversationKnowledgeMount.conversation_id == conversation_id
        )
    ).all()
    desired = set(normalized_space_ids)
    for mount in existing_mounts:
        if mount.space_id not in desired:
            session.delete(mount)

    existing_space_ids = {mount.space_id for mount in existing_mounts}
    for space_id in normalized_space_ids:
        if space_id in existing_space_ids:
            continue
        session.add(
            ConversationKnowledgeMount(
                conversation_id=conversation_id,
                space_id=space_id,
                created_by="user",
            )
        )

    session.commit()
    return list_conversation_knowledge_mounts(session, conversation_id)


def add_conversation_knowledge_mount(
    session: Session,
    conversation_id: int,
    space_id: int,
) -> ConversationKnowledgeMountRead:
    _get_conversation_or_404(session, conversation_id)
    get_active_space_or_404(session, space_id)
    existing = session.exec(
        select(ConversationKnowledgeMount).where(
            ConversationKnowledgeMount.conversation_id == conversation_id,
            ConversationKnowledgeMount.space_id == space_id,
        )
    ).first()
    if existing is not None:
        return to_mount_read(session, existing)

    mount = ConversationKnowledgeMount(
        conversation_id=conversation_id,
        space_id=space_id,
        created_by="user",
    )
    session.add(mount)
    session.commit()
    session.refresh(mount)
    return to_mount_read(session, mount)


def delete_conversation_knowledge_mount(
    session: Session,
    conversation_id: int,
    space_id: int,
) -> None:
    _get_conversation_or_404(session, conversation_id)
    mount = session.exec(
        select(ConversationKnowledgeMount).where(
            ConversationKnowledgeMount.conversation_id == conversation_id,
            ConversationKnowledgeMount.space_id == space_id,
        )
    ).first()
    if mount is not None:
        session.delete(mount)
        session.commit()


def to_mount_read(session: Session, mount: ConversationKnowledgeMount) -> ConversationKnowledgeMountRead:
    space = session.get(KnowledgeSpace, mount.space_id)
    if space is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "KNOWLEDGE_SPACE_NOT_FOUND", "message": "Mounted knowledge space not found."},
        )
    documents = session.exec(
        select(KnowledgeDocument).where(
            KnowledgeDocument.space_id == mount.space_id,
            KnowledgeDocument.status != "deleted",
        )
    ).all()
    ready_count = sum(1 for document in documents if document.status == "ready")
    return ConversationKnowledgeMountRead(
        id=mount.id or 0,
        conversation_id=mount.conversation_id,
        space_id=mount.space_id,
        space_name=space.name,
        space_icon=space.icon,
        ready_document_count=ready_count,
        document_count=len(documents),
        created_by=mount.created_by,
        scope_note=mount.scope_note,
        created_at=mount.created_at,
    )


def _get_conversation_or_404(session: Session, conversation_id: int) -> Conversation:
    conversation = session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "CONVERSATION_NOT_FOUND", "message": "Conversation not found."},
        )
    return conversation
