from __future__ import annotations

from fastapi import HTTPException, status
from sqlmodel import Session, desc, select

from app.models.knowledge import ConversationKnowledgeMount, KnowledgeDocument, KnowledgeSpace
from app.models.note import utc_now
from app.schemas.knowledge import KnowledgeSpaceCreate, KnowledgeSpaceRead, KnowledgeSpaceUpdate


ALLOWED_SPACE_STATUSES = {"active", "archived"}


def create_knowledge_space(session: Session, payload: KnowledgeSpaceCreate) -> KnowledgeSpaceRead:
    space = KnowledgeSpace(
        name=_normalize_name(payload.name),
        description=(payload.description or "").strip(),
        icon=(payload.icon or "").strip()[:80] or None,
        status="active",
    )
    session.add(space)
    session.commit()
    session.refresh(space)
    return to_space_read(session, space)


def list_knowledge_spaces(
    session: Session,
    *,
    include_archived: bool = False,
) -> list[KnowledgeSpaceRead]:
    statement = select(KnowledgeSpace)
    if not include_archived:
        statement = statement.where(KnowledgeSpace.status == "active")
    spaces = session.exec(
        statement.order_by(desc(KnowledgeSpace.updated_at), desc(KnowledgeSpace.id))
    ).all()
    return [to_space_read(session, space) for space in spaces]


def get_knowledge_space(session: Session, space_id: int) -> KnowledgeSpaceRead:
    return to_space_read(session, get_space_or_404(session, space_id))


def update_knowledge_space(
    session: Session,
    space_id: int,
    payload: KnowledgeSpaceUpdate,
) -> KnowledgeSpaceRead:
    if not payload.model_fields_set:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field is required.",
        )

    space = get_space_or_404(session, space_id)
    if "name" in payload.model_fields_set and payload.name is not None:
        space.name = _normalize_name(payload.name)
    if "description" in payload.model_fields_set:
        space.description = (payload.description or "").strip()
    if "icon" in payload.model_fields_set:
        space.icon = (payload.icon or "").strip()[:80] or None
    if "status" in payload.model_fields_set and payload.status is not None:
        next_status = _validate_space_status(payload.status)
        space.status = next_status
        if next_status == "archived":
            _delete_mounts_for_space(session, space_id)

    space.updated_at = utc_now()
    session.add(space)
    session.commit()
    session.refresh(space)
    return to_space_read(session, space)


def archive_knowledge_space(session: Session, space_id: int) -> KnowledgeSpaceRead:
    space = get_space_or_404(session, space_id)
    if space.status != "archived":
        space.status = "archived"
        space.updated_at = utc_now()
        session.add(space)
        _delete_mounts_for_space(session, space_id)
        session.commit()
        session.refresh(space)
    return to_space_read(session, space)


def get_active_space_or_404(session: Session, space_id: int) -> KnowledgeSpace:
    space = get_space_or_404(session, space_id)
    if space.status != "active":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "KNOWLEDGE_SPACE_ARCHIVED", "message": "Archived knowledge spaces cannot be mounted."},
        )
    return space


def get_space_or_404(session: Session, space_id: int) -> KnowledgeSpace:
    space = session.get(KnowledgeSpace, space_id)
    if space is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"code": "KNOWLEDGE_SPACE_NOT_FOUND", "message": "Knowledge space not found."},
        )
    return space


def to_space_read(session: Session, space: KnowledgeSpace) -> KnowledgeSpaceRead:
    documents = session.exec(
        select(KnowledgeDocument).where(
            KnowledgeDocument.space_id == (space.id or 0),
            KnowledgeDocument.status != "deleted",
        )
    ).all()
    ready_count = sum(1 for document in documents if document.status == "ready")
    return KnowledgeSpaceRead(
        id=space.id or 0,
        name=space.name,
        description=space.description,
        icon=space.icon,
        status=space.status,
        document_count=len(documents),
        ready_document_count=ready_count,
        created_at=space.created_at,
        updated_at=space.updated_at,
    )


def _delete_mounts_for_space(session: Session, space_id: int) -> None:
    mounts = session.exec(
        select(ConversationKnowledgeMount).where(ConversationKnowledgeMount.space_id == space_id)
    ).all()
    for mount in mounts:
        session.delete(mount)


def _normalize_name(value: str) -> str:
    normalized = value.strip()
    if not normalized:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Knowledge space name cannot be empty.")
    return normalized[:120]


def _validate_space_status(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in ALLOWED_SPACE_STATUSES:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid knowledge space status.")
    return normalized
