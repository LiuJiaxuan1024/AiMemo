from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlmodel import Session, col, desc, select

from app.jobs.models import GraphName, JobType
from app.jobs.queue import enqueue_job
from app.models.job import Job
from app.models.note import Note
from app.models.note_chunk import NoteChunk
from app.rag.hashing import content_hash
from app.rag.vector_store import delete_note_chunk_embeddings
from app.schemas.note import NoteCreate, NoteListItem, NoteRead, NoteUpdate


ALLOWED_NOTE_STATUSES = {"active", "deleted"}


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
        content_hash=note.content_hash,
        summary=note.summary,
        tags=_decode_tags(note.tags),
        status=note.status,
        processing_status=note.processing_status,
        processing_error=note.processing_error,
        processed_at=note.processed_at,
        embedding_status=note.embedding_status,
        embedding_error=note.embedding_error,
        embedded_at=note.embedded_at,
        deleted_at=note.deleted_at,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


def _to_note_list_item(note: Note) -> NoteListItem:
    return NoteListItem(
        id=note.id or 0,
        title=note.title,
        content_hash=note.content_hash,
        summary=note.summary,
        tags=_decode_tags(note.tags),
        status=note.status,
        processing_status=note.processing_status,
        processing_error=note.processing_error,
        processed_at=note.processed_at,
        embedding_status=note.embedding_status,
        embedding_error=note.embedding_error,
        embedded_at=note.embedded_at,
        deleted_at=note.deleted_at,
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


def create_note(session: Session, payload: NoteCreate) -> NoteRead:
    """创建笔记并为当前内容版本创建后台任务。

    dedupe_key 带 content_hash，确保用户快速修改后，新旧任务不会互相覆盖。
    """

    normalized_content = _normalize_content(payload.content)
    user_title = payload.title.strip()
    title = user_title or _fallback_title(normalized_content)
    summary = payload.summary.strip()
    tags = payload.tags
    note_hash = build_note_content_hash(normalized_content)

    note = Note(
        title=title,
        title_source="user" if user_title else "fallback",
        content=normalized_content,
        content_hash=note_hash,
        summary=summary,
        tags=_encode_tags(tags),
        status="active",
        processing_status="pending",
        embedding_status="pending",
    )
    session.add(note)
    session.flush()
    if note.id is None:
        raise RuntimeError("Note id was not generated before enqueueing note jobs.")
    enqueue_note_processing_jobs(session, note)
    session.commit()
    session.refresh(note)
    return _to_note_read(note)


def list_notes(session: Session, *, status_filter: str = "active") -> list[NoteListItem]:
    """按状态列出笔记。

    默认只显示 active；最近删除使用 status=deleted 单独读取。
    """

    normalized_status = _validate_note_status(status_filter)
    notes = session.exec(
        select(Note)
        .where(Note.status == normalized_status)
        .order_by(desc(Note.updated_at), desc(Note.id))
    ).all()
    return [_to_note_list_item(note) for note in notes]


def get_note(session: Session, note_id: int) -> NoteRead:
    return _to_note_read(_get_note_or_404(session, note_id))


def update_note(session: Session, note_id: int, payload: NoteUpdate) -> NoteRead:
    """更新笔记。

    content 变化会清理旧 chunks/vector，并重新创建 metadata + embedding jobs。
    旧 job 执行时还会通过 content_hash 二次校验，所以不会把旧结果写回新笔记。
    """

    if not payload.model_fields_set:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field is required.",
        )

    note = _get_note_or_404(session, note_id)
    if note.status != "active":
        raise _bad_request("Deleted notes must be restored before editing.")

    content_changed = False
    if "title" in payload.model_fields_set:
        user_title = (payload.title or "").strip()
        note.title = user_title or _fallback_title(note.content)
        note.title_source = "user" if user_title else "fallback"

    if "content" in payload.model_fields_set:
        if payload.content is None:
            raise _bad_request("content cannot be null.")
        next_content = _normalize_content(payload.content)
        next_hash = build_note_content_hash(next_content)
        content_changed = next_hash != note.content_hash
        note.content = next_content
        note.content_hash = next_hash
        if note.title_source != "user":
            note.title = _fallback_title(next_content)
            note.title_source = "fallback"

    if content_changed:
        _reset_ai_fields_for_rebuild(note)
        _delete_note_chunks_and_vectors(session, note.id or 0)
        enqueue_note_processing_jobs(session, note)

    note.updated_at = utc_now()
    session.add(note)
    session.commit()
    session.refresh(note)
    return _to_note_read(note)


def delete_note(session: Session, note_id: int) -> NoteRead:
    """软删除笔记，进入最近删除。

    chunks/vector 暂时保留；检索层必须 join note.status=active，保证删除后 AI 不再召回。
    """

    note = _get_note_or_404(session, note_id)
    note.status = "deleted"
    note.deleted_at = utc_now()
    note.updated_at = utc_now()
    session.add(note)
    session.commit()
    session.refresh(note)
    return _to_note_read(note)


def restore_note(session: Session, note_id: int) -> NoteRead:
    """从最近删除恢复笔记。

    如果 chunks 仍存在，恢复后可立即被 RAG 使用；如果未来清理过 chunks，则补建 embedding。
    """

    note = _get_note_or_404(session, note_id)
    if note.status != "deleted":
        return _to_note_read(note)

    note.status = "active"
    note.deleted_at = None
    note.updated_at = utc_now()

    chunk_count = session.exec(
        select(NoteChunk).where(NoteChunk.note_id == (note.id or 0))
    ).all()
    if not chunk_count:
        note.embedding_status = "pending"
        note.embedding_error = ""
        note.embedded_at = None
        enqueue_note_embedding_job(session, note)

    session.add(note)
    session.commit()
    session.refresh(note)
    return _to_note_read(note)


def hard_delete_note(session: Session, note_id: int) -> None:
    """永久删除最近删除中的笔记和它的 chunks/vector。"""

    note = _get_note_or_404(session, note_id)
    if note.status != "deleted":
        raise _bad_request("Only recently deleted notes can be permanently deleted.")
    _delete_note_chunks_and_vectors(session, note_id)
    session.delete(note)
    session.commit()


def enqueue_note_processing_jobs(session: Session, note: Note) -> None:
    """为笔记当前内容版本创建 metadata 和 embedding jobs。"""

    enqueue_note_metadata_job(session, note)
    enqueue_note_embedding_job(session, note)


def enqueue_note_metadata_job(session: Session, note: Note) -> Job:
    if note.id is None:
        raise RuntimeError("Note id is required before enqueueing metadata job.")
    return enqueue_job(
        session,
        job_type=JobType.NOTE_METADATA.value,
        graph_name=GraphName.NOTE_METADATA.value,
        payload={"note_id": note.id, "content_hash": note.content_hash},
        dedupe_key=_note_job_dedupe_key(JobType.NOTE_METADATA.value, note),
    )


def enqueue_note_embedding_job(session: Session, note: Note) -> Job:
    if note.id is None:
        raise RuntimeError("Note id is required before enqueueing embedding job.")
    return enqueue_job(
        session,
        job_type=JobType.NOTE_EMBEDDING.value,
        graph_name=GraphName.NOTE_EMBEDDING.value,
        payload={"note_id": note.id, "content_hash": note.content_hash},
        dedupe_key=_note_job_dedupe_key(JobType.NOTE_EMBEDDING.value, note),
    )


def build_note_content_hash(content: str) -> str:
    return content_hash(content.strip())


def touch_note(note: Note) -> None:
    note.updated_at = datetime.now(timezone.utc)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _reset_ai_fields_for_rebuild(note: Note) -> None:
    note.summary = ""
    note.tags = ""
    note.processing_status = "pending"
    note.processing_error = ""
    note.processed_at = None
    note.embedding_status = "pending"
    note.embedding_error = ""
    note.embedded_at = None


def _delete_note_chunks_and_vectors(session: Session, note_id: int) -> None:
    # 这里必须避免 SQLAlchemy/SQLModel 在查询旧 chunks 前自动 flush 当前 note 修改。
    # update_note 已经把 note.content/content_hash 标记为 dirty；如果此处自动 flush，
    # SQLite 主连接会先拿到写锁。随后 vector_store 用独立 sqlite3 连接删除 sqlite-vec
    # 向量行时就会触发 `database is locked`。no_autoflush 让向量删除先完成，
    # 再由同一个业务 session 删除 chunks 并统一提交。
    with session.no_autoflush:
        old_chunks = session.exec(select(NoteChunk).where(NoteChunk.note_id == note_id)).all()
    delete_note_chunk_embeddings([chunk.id for chunk in old_chunks if chunk.id is not None])
    for chunk in old_chunks:
        session.delete(chunk)
    session.flush()


def _get_note_or_404(session: Session, note_id: int) -> Note:
    note = session.get(Note, note_id)
    if note is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Note not found",
        )
    return note


def _normalize_content(content: str) -> str:
    normalized = content.strip()
    if not normalized:
        raise _bad_request("content cannot be empty.")
    return normalized


def _validate_note_status(note_status: str) -> str:
    normalized = note_status.strip().lower()
    if normalized not in ALLOWED_NOTE_STATUSES:
        raise _bad_request("Invalid note status.")
    return normalized


def _note_job_dedupe_key(job_type: str, note: Note) -> str:
    return f"{job_type}:note:{note.id}:content:{note.content_hash}"


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
