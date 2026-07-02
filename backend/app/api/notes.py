from fastapi import APIRouter, Depends, Query, Response, status
from sqlmodel import Session

from app.core.database import get_session
from app.schemas.note import NoteCreate, NoteListItem, NoteRead, NoteUpdate
from app.services.note_service import (
    create_note,
    delete_note,
    get_note,
    hard_delete_note,
    list_notes,
    restore_note,
    update_note,
)


router = APIRouter(prefix="/notes", tags=["notes"])


@router.post("", response_model=NoteRead, status_code=status.HTTP_201_CREATED)
def create_note_api(
    payload: NoteCreate,
    session: Session = Depends(get_session),
) -> NoteRead:
    return create_note(session, payload)


@router.get("", response_model=list[NoteListItem])
def list_notes_api(
    status: str = Query(default="active"),
    category_id: str | None = Query(default=None),
    tag: str | None = Query(default=None),
    favorite: bool | None = Query(default=None),
    pinned: bool | None = Query(default=None),
    processing_status: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> list[NoteListItem]:
    normalized_category_id: int | str | None = category_id
    if category_id and category_id != "uncategorized":
        normalized_category_id = int(category_id)
    return list_notes(
        session,
        status_filter=status,
        category_id=normalized_category_id,
        tag=tag,
        favorite=favorite,
        pinned=pinned,
        processing_status=processing_status,
    )


@router.get("/{note_id}", response_model=NoteRead)
def get_note_api(
    note_id: int,
    session: Session = Depends(get_session),
) -> NoteRead:
    return get_note(session, note_id)


@router.patch("/{note_id}", response_model=NoteRead)
def update_note_api(
    note_id: int,
    payload: NoteUpdate,
    session: Session = Depends(get_session),
) -> NoteRead:
    return update_note(session, note_id, payload)


@router.delete("/{note_id}", response_model=NoteRead)
def delete_note_api(
    note_id: int,
    session: Session = Depends(get_session),
) -> NoteRead:
    return delete_note(session, note_id)


@router.post("/{note_id}/restore", response_model=NoteRead)
def restore_note_api(
    note_id: int,
    session: Session = Depends(get_session),
) -> NoteRead:
    return restore_note(session, note_id)


@router.delete("/{note_id}/hard", status_code=status.HTTP_204_NO_CONTENT)
def hard_delete_note_api(
    note_id: int,
    session: Session = Depends(get_session),
) -> Response:
    hard_delete_note(session, note_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
