from fastapi import APIRouter, Depends
from sqlmodel import Session

from app.core.database import get_session
from app.schemas.note import NoteTagDelete, NoteTagMerge, NoteTagRead, NoteTagRename
from app.services.note_service import delete_note_tag, list_note_tags, merge_note_tags, rename_note_tag


router = APIRouter(prefix="/note-tags", tags=["note-tags"])


@router.get("", response_model=list[NoteTagRead])
def list_note_tags_api(session: Session = Depends(get_session)) -> list[NoteTagRead]:
    return list_note_tags(session)


@router.post("/rename", response_model=list[NoteTagRead])
def rename_note_tag_api(
    payload: NoteTagRename,
    session: Session = Depends(get_session),
) -> list[NoteTagRead]:
    return rename_note_tag(session, old_tag=payload.old_tag, new_tag=payload.new_tag)


@router.post("/merge", response_model=list[NoteTagRead])
def merge_note_tags_api(
    payload: NoteTagMerge,
    session: Session = Depends(get_session),
) -> list[NoteTagRead]:
    return merge_note_tags(session, source_tags=payload.source_tags, target_tag=payload.target_tag)


@router.post("/delete", response_model=list[NoteTagRead])
def delete_note_tag_api(
    payload: NoteTagDelete,
    session: Session = Depends(get_session),
) -> list[NoteTagRead]:
    return delete_note_tag(session, tag=payload.tag)
