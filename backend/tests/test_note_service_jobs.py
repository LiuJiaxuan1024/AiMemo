from sqlmodel import select

from app.jobs.models import GraphName, JobStatus, JobType
from app.models.job import Job
from app.models.note import Note
from app.models.note_chunk import NoteChunk
from app.schemas.note import NoteCreate
from app.services.note_service import (
    build_note_content_hash,
    create_note,
    delete_note,
    hard_delete_note,
    list_notes,
    restore_note,
    update_note,
)
from app.schemas.note import NoteUpdate


def test_create_note_returns_immediately_with_pending_job(session):
    note = create_note(
        session,
        NoteCreate(content="今天想先把 Ai 记的后台任务队列做出来。"),
    )

    jobs = session.exec(select(Job).order_by(Job.id)).all()
    assert note.processing_status == "pending"
    assert note.embedding_status == "pending"
    assert note.content_hash == build_note_content_hash("今天想先把 Ai 记的后台任务队列做出来。")
    db_note = session.get(Note, note.id)
    assert db_note.sync_status == "dirty"
    assert db_note.local_revision == 1
    assert note.summary == ""
    assert note.tags == []
    assert [job.type for job in jobs] == [
        JobType.NOTE_METADATA.value,
        JobType.NOTE_EMBEDDING.value,
    ]
    assert [job.status for job in jobs] == [JobStatus.PENDING.value, JobStatus.PENDING.value]
    assert [job.graph_name for job in jobs] == [
        GraphName.NOTE_METADATA.value,
        GraphName.NOTE_EMBEDDING.value,
    ]
    assert all(job.thread_id == f"job:{job.id}" for job in jobs)
    assert all(note.content_hash in str(job.payload) for job in jobs)


def test_create_note_dual_stores_markdown_and_blocks(session):
    note = create_note(
        session,
        NoteCreate(
            content_markdown="## 标题\n\n正文",
            content_blocks='[{"type":"heading","content":"标题"}]',
            content_format="blocknote",
        ),
    )

    assert note.content == "## 标题\n\n正文"
    assert note.content_markdown == "## 标题\n\n正文"
    assert note.content_blocks == '[{"type":"heading","content":"标题"}]'
    assert note.content_format == "blocknote"
    assert note.content_version == 1
    assert note.content_hash == build_note_content_hash("## 标题\n\n正文")


def test_update_note_rebuilds_current_content_version_jobs_and_clears_chunks(session):
    note = create_note(session, NoteCreate(content="旧内容。"))
    old_hash = note.content_hash
    chunk = NoteChunk(
        note_id=note.id,
        chunk_index=0,
        content="旧内容。",
        content_hash="old",
        token_count=3,
        embedding_status="completed",
    )
    session.add(chunk)
    session.commit()

    updated = update_note(
        session,
        note.id,
        NoteUpdate(title="新标题", content="新内容。"),
    )

    jobs = session.exec(select(Job).order_by(Job.id)).all()
    chunks = session.exec(select(NoteChunk).where(NoteChunk.note_id == note.id)).all()
    assert updated.title == "新标题"
    assert updated.content == "新内容。"
    assert updated.content_hash != old_hash
    assert updated.processing_status == "pending"
    assert updated.embedding_status == "pending"
    assert chunks == []
    assert [job.dedupe_key for job in jobs[-2:]] == [
        f"{JobType.NOTE_METADATA.value}:note:{note.id}:content:{updated.content_hash}",
        f"{JobType.NOTE_EMBEDDING.value}:note:{note.id}:content:{updated.content_hash}",
    ]


def test_update_note_dual_store_fields_without_rebuild_when_markdown_same(session):
    note = create_note(session, NoteCreate(content="同一份 Markdown。"))
    db_note = session.get(Note, note.id)
    original_revision = db_note.local_revision

    updated = update_note(
        session,
        note.id,
        NoteUpdate(
            content_markdown="同一份 Markdown。",
            content_blocks='[{"type":"paragraph"}]',
            content_format="blocknote",
        ),
    )

    assert updated.content == "同一份 Markdown。"
    assert updated.content_markdown == "同一份 Markdown。"
    assert updated.content_blocks == '[{"type":"paragraph"}]'
    assert updated.content_format == "blocknote"
    assert updated.content_version == 1
    db_note = session.get(Note, note.id)
    assert db_note.sync_status == "dirty"
    assert db_note.local_revision == original_revision + 1


def test_delete_restore_and_hard_delete_note(session):
    note = create_note(session, NoteCreate(content="可以恢复的笔记。"))
    chunk = NoteChunk(
        note_id=note.id,
        chunk_index=0,
        content="可以恢复的笔记。",
        content_hash="chunk",
        token_count=6,
        embedding_status="completed",
    )
    session.add(chunk)
    session.commit()

    deleted = delete_note(session, note.id)

    assert deleted.status == "deleted"
    assert deleted.deleted_at is not None
    assert list_notes(session) == []
    assert [item.id for item in list_notes(session, status_filter="deleted")] == [note.id]
    assert session.exec(select(NoteChunk).where(NoteChunk.note_id == note.id)).all()

    restored = restore_note(session, note.id)

    assert restored.status == "active"
    assert restored.deleted_at is None

    delete_note(session, note.id)
    hard_delete_note(session, note.id)

    assert session.get(Note, note.id) is None
    assert session.exec(select(NoteChunk).where(NoteChunk.note_id == note.id)).all() == []
