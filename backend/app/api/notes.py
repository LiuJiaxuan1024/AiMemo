from fastapi import APIRouter, Depends, status
from sqlmodel import Session

from app.core.database import get_session
from app.schemas.note import NoteCreate, NoteListItem, NoteRead
from app.services.note_service import create_note, get_note, list_notes


router = APIRouter(prefix="/notes", tags=["notes"])


@router.post("", response_model=NoteRead, status_code=status.HTTP_201_CREATED)
def create_note_api(
    payload: NoteCreate,
    session: Session = Depends(get_session),
) -> NoteRead:
    return create_note(session, payload)


@router.get("", response_model=list[NoteListItem])
def list_notes_api(session: Session = Depends(get_session)) -> list[NoteListItem]:
    return list_notes(session)


@router.get("/{note_id}", response_model=NoteRead)
def get_note_api(
    note_id: int,
    session: Session = Depends(get_session),
) -> NoteRead:
    return get_note(session, note_id)
