from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlmodel import Session, desc, select

from app.jobs.models import GraphName, JobType
from app.jobs.queue import enqueue_job
from app.models.note import Note
from app.schemas.note import NoteCreate, NoteListItem, NoteRead


def _encode_tags(tags: list[str]) -> str:
    return ",".join(tag.strip() for tag in tags if tag.strip())


def _decode_tags(tags: str) -> list[str]:
    if not tags:
        return []
    return [tag for tag in tags.split(",") if tag]


def _fallback_title(content: str) -> str:
    first_line = content.strip().splitlines()[0]
    return first_line[:60] if first_line else "未命名笔记"


def _to_note_read(note: Note) -> NoteRead:
    return NoteRead(
        id=note.id or 0,
        title=note.title,
        content=note.content,
        summary=note.summary,
        tags=_decode_tags(note.tags),
        processing_status=note.processing_status,
        processing_error=note.processing_error,
        processed_at=note.processed_at,
        embedding_status=note.embedding_status,
        embedding_error=note.embedding_error,
        embedded_at=note.embedded_at,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


def _to_note_list_item(note: Note) -> NoteListItem:
    return NoteListItem(
        id=note.id or 0,
        title=note.title,
        summary=note.summary,
        tags=_decode_tags(note.tags),
        processing_status=note.processing_status,
        processing_error=note.processing_error,
        processed_at=note.processed_at,
        embedding_status=note.embedding_status,
        embedding_error=note.embedding_error,
        embedded_at=note.embedded_at,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


def create_note(session: Session, payload: NoteCreate) -> NoteRead:
    user_title = payload.title.strip()
    title = user_title or _fallback_title(payload.content)
    summary = payload.summary.strip()
    tags = payload.tags

    note = Note(
        title=title,
        title_source="user" if user_title else "fallback",
        content=payload.content,
        summary=summary,
        tags=_encode_tags(tags),
        processing_status="pending",
    )
    session.add(note)
    session.flush()
    if note.id is None:
        raise RuntimeError("Note id was not generated before enqueueing metadata job.")
    enqueue_job(
        session,
        job_type=JobType.NOTE_METADATA.value,
        graph_name=GraphName.NOTE_METADATA.value,
        payload={"note_id": note.id},
        dedupe_key=f"{JobType.NOTE_METADATA.value}:note:{note.id}",
    )
    enqueue_job(
        session,
        job_type=JobType.NOTE_EMBEDDING.value,
        graph_name=GraphName.NOTE_EMBEDDING.value,
        payload={"note_id": note.id},
        dedupe_key=f"{JobType.NOTE_EMBEDDING.value}:note:{note.id}",
    )
    session.commit()
    session.refresh(note)
    return _to_note_read(note)


def list_notes(session: Session) -> list[NoteListItem]:
    notes = session.exec(select(Note).order_by(desc(Note.updated_at))).all()
    return [_to_note_list_item(note) for note in notes]


def get_note(session: Session, note_id: int) -> NoteRead:
    note = session.get(Note, note_id)
    if note is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Note not found",
        )
    return _to_note_read(note)


def touch_note(note: Note) -> None:
    note.updated_at = datetime.now(timezone.utc)
