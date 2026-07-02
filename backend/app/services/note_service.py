from datetime import datetime, timezone

from fastapi import HTTPException, status
from sqlmodel import Session, col, desc, select

from app.jobs.models import GraphName, JobType
from app.jobs.queue import enqueue_job
from app.models.job import Job
from app.models.note import Note, NoteCategory
from app.models.note_chunk import NoteChunk
from app.rag.hashing import content_hash
from app.rag.vector_store import delete_note_chunk_embeddings
from app.schemas.note import NoteCategoryRead, NoteCreate, NoteListItem, NoteRead, NoteTagRead, NoteUpdate
from app.services.cloud_sync_service import mark_note_dirty


ALLOWED_NOTE_STATUSES = {"active", "deleted"}


def _encode_tags(tags: list[str]) -> str:
    return ",".join(_normalize_tags(tags))


def _decode_tags(tags: str) -> list[str]:
    if not tags:
        return []
    return [tag for tag in tags.split(",") if tag]


def _normalize_tags(tags: list[str]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw_tag in tags:
        tag = str(raw_tag).strip()
        if not tag or tag in seen:
            continue
        seen.add(tag)
        normalized.append(tag)
    return normalized


def _fallback_title(content: str) -> str:
    first_line = content.strip().splitlines()[0]
    return first_line[:60] if first_line else "未命名笔记"


def _to_note_read(note: Note, *, category_name: str = "") -> NoteRead:
    markdown = _note_markdown(note)
    return NoteRead(
        id=note.id or 0,
        title=note.title,
        content=markdown,
        content_markdown=markdown,
        content_blocks=note.content_blocks or "",
        content_format=note.content_format or "markdown",
        content_version=note.content_version or 1,
        content_hash=note.content_hash,
        summary=note.summary,
        tags=_decode_tags(note.tags),
        category_id=note.category_id,
        category_name=category_name,
        is_favorite=bool(note.is_favorite),
        pinned_at=note.pinned_at,
        archived_at=note.archived_at,
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


def _to_note_list_item(note: Note, *, category_name: str = "") -> NoteListItem:
    return NoteListItem(
        id=note.id or 0,
        title=note.title,
        content_hash=note.content_hash,
        summary=note.summary,
        tags=_decode_tags(note.tags),
        category_id=note.category_id,
        category_name=category_name,
        is_favorite=bool(note.is_favorite),
        pinned_at=note.pinned_at,
        archived_at=note.archived_at,
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


def _to_category_read(category: NoteCategory, *, note_count: int = 0) -> NoteCategoryRead:
    return NoteCategoryRead(
        id=category.id or 0,
        name=category.name,
        description=category.description,
        color=category.color,
        sort_order=category.sort_order,
        status=category.status,
        note_count=note_count,
        created_at=category.created_at,
        updated_at=category.updated_at,
    )


def create_note(session: Session, payload: NoteCreate) -> NoteRead:
    """创建笔记并为当前内容版本创建后台任务。

    dedupe_key 带 content_hash，确保用户快速修改后，新旧任务不会互相覆盖。
    """

    normalized_content = _normalize_content(_payload_markdown(payload.content_markdown, payload.content))
    user_title = payload.title.strip()
    title = user_title or _fallback_title(normalized_content)
    summary = payload.summary.strip()
    tags = payload.tags
    content_format = _normalize_content_format(payload.content_format)
    note_hash = build_note_content_hash(normalized_content)
    category = _get_active_category_or_400(session, payload.category_id) if payload.category_id else None

    note = Note(
        title=title,
        title_source="user" if user_title else "fallback",
        content=normalized_content,
        content_markdown=normalized_content,
        content_blocks=payload.content_blocks.strip(),
        content_format=content_format,
        content_version=1,
        content_hash=note_hash,
        summary=summary,
        tags=_encode_tags(tags),
        category_id=category.id if category else None,
        is_favorite=bool(payload.is_favorite),
        pinned_at=utc_now() if payload.pinned else None,
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
    return _to_note_read(note, category_name=category.name if category else "")


def list_notes(
    session: Session,
    *,
    status_filter: str = "active",
    category_id: int | str | None = None,
    tag: str | None = None,
    favorite: bool | None = None,
    pinned: bool | None = None,
    processing_status: str | None = None,
) -> list[NoteListItem]:
    """按状态列出笔记。

    默认只显示 active；最近删除使用 status=deleted 单独读取。
    """

    normalized_status = _validate_note_status(status_filter)
    statement = (
        select(Note)
        .where(Note.status == normalized_status)
        .order_by(desc(Note.updated_at), desc(Note.id))
    )
    if isinstance(category_id, int):
        statement = statement.where(Note.category_id == category_id)
    if category_id == "uncategorized":
        statement = statement.where(Note.category_id == None)  # noqa: E711
    if favorite is True:
        statement = statement.where(Note.is_favorite == True)  # noqa: E712
    if pinned is True:
        statement = statement.where(Note.pinned_at != None)  # noqa: E711
    if processing_status:
        statement = statement.where(Note.processing_status == processing_status.strip())

    notes = session.exec(statement).all()
    normalized_tag = (tag or "").strip()
    if normalized_tag:
        notes = [note for note in notes if normalized_tag in _decode_tags(note.tags)]
    category_names = _category_name_map(session, notes)
    return [_to_note_list_item(note, category_name=category_names.get(note.category_id or 0, "")) for note in notes]


def get_note(session: Session, note_id: int) -> NoteRead:
    note = _get_note_or_404(session, note_id)
    return _to_note_read(note, category_name=_category_name(session, note.category_id))


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

    markdown_touched = "content" in payload.model_fields_set or "content_markdown" in payload.model_fields_set
    if markdown_touched:
        next_content = _normalize_content(_payload_markdown(payload.content_markdown, payload.content))
        next_hash = build_note_content_hash(next_content)
        content_changed = next_hash != note.content_hash
        note.content = next_content
        note.content_markdown = next_content
        note.content_hash = next_hash
        if note.title_source != "user":
            note.title = _fallback_title(next_content)
            note.title_source = "fallback"

    if "content_blocks" in payload.model_fields_set:
        note.content_blocks = payload.content_blocks or ""

    if "content_format" in payload.model_fields_set:
        note.content_format = _normalize_content_format(payload.content_format or "markdown")

    if "category_id" in payload.model_fields_set:
        category = _get_active_category_or_400(session, payload.category_id) if payload.category_id else None
        note.category_id = category.id if category else None

    if "tags" in payload.model_fields_set and payload.tags is not None:
        note.tags = _encode_tags(payload.tags)

    if "is_favorite" in payload.model_fields_set and payload.is_favorite is not None:
        note.is_favorite = bool(payload.is_favorite)

    if "pinned" in payload.model_fields_set and payload.pinned is not None:
        note.pinned_at = utc_now() if payload.pinned else None

    if content_changed:
        note.content_version = (note.content_version or 1) + 1
        _reset_ai_fields_for_rebuild(note)
        _delete_note_chunks_and_vectors(session, note.id or 0)
        enqueue_note_processing_jobs(session, note)

    note.updated_at = utc_now()
    mark_note_dirty(note)
    session.add(note)
    session.commit()
    session.refresh(note)
    return _to_note_read(note, category_name=_category_name(session, note.category_id))


def delete_note(session: Session, note_id: int) -> NoteRead:
    """软删除笔记，进入最近删除。

    chunks/vector 暂时保留；检索层必须 join note.status=active，保证删除后 AI 不再召回。
    """

    note = _get_note_or_404(session, note_id)
    note.status = "deleted"
    note.deleted_at = utc_now()
    note.updated_at = utc_now()
    mark_note_dirty(note)
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
    mark_note_dirty(note)

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
    """隐藏最近删除中的笔记，并保留可同步 tombstone。

    直接物理删除会让云同步失去删除事件。这里清理正文和索引，让本机不再展示和检索，
    但保留一条最小 tombstone 记录，等待下一次同步传播永久删除语义。
    """

    note = _get_note_or_404(session, note_id)
    if note.status != "deleted":
        raise _bad_request("Only recently deleted notes can be permanently deleted.")
    _delete_note_chunks_and_vectors(session, note_id)
    note.title = ""
    note.summary = ""
    note.tags = ""
    note.content = ""
    note.content_markdown = ""
    note.content_blocks = ""
    note.content_hash = content_hash("")
    note.category_id = None
    note.is_favorite = False
    note.pinned_at = None
    note.archived_at = None
    note.status = "purged"
    note.updated_at = utc_now()
    mark_note_dirty(note)
    session.add(note)
    session.commit()


def create_note_category(
    session: Session,
    *,
    name: str,
    description: str = "",
    color: str = "",
) -> NoteCategoryRead:
    category = NoteCategory(
        name=_normalize_category_name(name),
        description=description.strip(),
        color=color.strip(),
        sort_order=_next_category_sort_order(session),
    )
    session.add(category)
    session.commit()
    session.refresh(category)
    return _to_category_read(category)


def list_note_categories(session: Session) -> list[NoteCategoryRead]:
    categories = session.exec(
        select(NoteCategory)
        .where(NoteCategory.status == "active")
        .order_by(NoteCategory.sort_order, NoteCategory.created_at, NoteCategory.id)
    ).all()
    counts = _category_note_counts(session)
    return [_to_category_read(category, note_count=counts.get(category.id or 0, 0)) for category in categories]


def update_note_category(
    session: Session,
    category_id: int,
    *,
    name: str | None = None,
    description: str | None = None,
    color: str | None = None,
    sort_order: int | None = None,
) -> NoteCategoryRead:
    category = _get_category_or_404(session, category_id)
    if category.status != "active":
        raise _bad_request("Deleted categories cannot be updated.")
    if name is not None:
        category.name = _normalize_category_name(name)
    if description is not None:
        category.description = description.strip()
    if color is not None:
        category.color = color.strip()
    if sort_order is not None:
        category.sort_order = int(sort_order)
    category.updated_at = utc_now()
    _mark_category_dirty(category)
    session.add(category)
    session.commit()
    session.refresh(category)
    return _to_category_read(category, note_count=_category_note_counts(session).get(category.id or 0, 0))


def delete_note_category(session: Session, category_id: int) -> NoteCategoryRead:
    category = _get_category_or_404(session, category_id)
    category.status = "deleted"
    category.deleted_at = utc_now()
    category.updated_at = utc_now()
    _mark_category_dirty(category)
    notes = session.exec(select(Note).where(Note.category_id == category_id)).all()
    for note in notes:
        note.category_id = None
        note.updated_at = utc_now()
        mark_note_dirty(note)
        session.add(note)
    session.add(category)
    session.commit()
    session.refresh(category)
    return _to_category_read(category)


def list_note_tags(session: Session) -> list[NoteTagRead]:
    counts = _tag_note_counts(session)
    return [NoteTagRead(name=name, note_count=count) for name, count in sorted(counts.items())]


def rename_note_tag(session: Session, *, old_tag: str, new_tag: str) -> list[NoteTagRead]:
    old_value = _normalize_single_tag(old_tag)
    new_value = _normalize_single_tag(new_tag)
    if old_value == new_value:
        return list_note_tags(session)
    notes = session.exec(select(Note)).all()
    for note in notes:
        tags = _decode_tags(note.tags)
        if old_value not in tags:
            continue
        note.tags = _encode_tags([new_value if tag == old_value else tag for tag in tags])
        note.updated_at = utc_now()
        mark_note_dirty(note)
        session.add(note)
    session.commit()
    return list_note_tags(session)


def merge_note_tags(session: Session, *, source_tags: list[str], target_tag: str) -> list[NoteTagRead]:
    sources = {_normalize_single_tag(tag) for tag in source_tags}
    target = _normalize_single_tag(target_tag)
    notes = session.exec(select(Note)).all()
    for note in notes:
        tags = _decode_tags(note.tags)
        if not any(tag in sources for tag in tags):
            continue
        note.tags = _encode_tags([target if tag in sources else tag for tag in tags])
        note.updated_at = utc_now()
        mark_note_dirty(note)
        session.add(note)
    session.commit()
    return list_note_tags(session)


def delete_note_tag(session: Session, *, tag: str) -> list[NoteTagRead]:
    target = _normalize_single_tag(tag)
    notes = session.exec(select(Note)).all()
    for note in notes:
        tags = _decode_tags(note.tags)
        if target not in tags:
            continue
        note.tags = _encode_tags([current for current in tags if current != target])
        note.updated_at = utc_now()
        mark_note_dirty(note)
        session.add(note)
    session.commit()
    return list_note_tags(session)


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


def _payload_markdown(content_markdown: str | None, legacy_content: str | None) -> str:
    content = content_markdown if content_markdown is not None else legacy_content
    if content is None:
        raise _bad_request("content_markdown or content is required.")
    return content


def _note_markdown(note: Note) -> str:
    return (note.content_markdown or note.content or "").strip()


def _normalize_content_format(content_format: str) -> str:
    normalized = content_format.strip().lower() or "markdown"
    if normalized not in {"markdown", "blocknote"}:
        raise _bad_request("Invalid content format.")
    return normalized


def _validate_note_status(note_status: str) -> str:
    normalized = note_status.strip().lower()
    if normalized not in ALLOWED_NOTE_STATUSES:
        raise _bad_request("Invalid note status.")
    return normalized


def _get_active_category_or_400(session: Session, category_id: int | None) -> NoteCategory:
    if category_id is None:
        raise _bad_request("category_id cannot be empty.")
    category = session.get(NoteCategory, category_id)
    if category is None or category.status != "active":
        raise _bad_request("Invalid note category.")
    return category


def _get_category_or_404(session: Session, category_id: int) -> NoteCategory:
    category = session.get(NoteCategory, category_id)
    if category is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note category not found")
    return category


def _category_name(session: Session, category_id: int | None) -> str:
    if category_id is None:
        return ""
    category = session.get(NoteCategory, category_id)
    if category is None or category.status != "active":
        return ""
    return category.name


def _category_name_map(session: Session, notes: list[Note]) -> dict[int, str]:
    category_ids = sorted({int(note.category_id) for note in notes if note.category_id is not None})
    if not category_ids:
        return {}
    categories = session.exec(
        select(NoteCategory).where(col(NoteCategory.id).in_(category_ids)).where(NoteCategory.status == "active")
    ).all()
    return {category.id or 0: category.name for category in categories}


def _category_note_counts(session: Session) -> dict[int, int]:
    notes = session.exec(select(Note).where(Note.status == "active")).all()
    counts: dict[int, int] = {}
    for note in notes:
        if note.category_id is None:
            continue
        counts[note.category_id] = counts.get(note.category_id, 0) + 1
    return counts


def _tag_note_counts(session: Session) -> dict[str, int]:
    notes = session.exec(select(Note).where(Note.status == "active")).all()
    counts: dict[str, int] = {}
    for note in notes:
        for tag in set(_decode_tags(note.tags)):
            counts[tag] = counts.get(tag, 0) + 1
    return counts


def _normalize_category_name(name: str) -> str:
    normalized = name.strip()
    if not normalized:
        raise _bad_request("Category name cannot be empty.")
    return normalized


def _normalize_single_tag(tag: str) -> str:
    normalized = tag.strip()
    if not normalized:
        raise _bad_request("Tag cannot be empty.")
    return normalized


def _next_category_sort_order(session: Session) -> int:
    categories = session.exec(select(NoteCategory)).all()
    if not categories:
        return 0
    return max(category.sort_order for category in categories) + 1


def _mark_category_dirty(category: NoteCategory) -> None:
    category.local_revision = max(int(category.local_revision or 0) + 1, 1)
    category.sync_status = "dirty"
    category.sync_conflict_id = ""
    category.last_synced_at = None


def _note_job_dedupe_key(job_type: str, note: Note) -> str:
    return f"{job_type}:note:{note.id}:content:{note.content_hash}"


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)
