from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime, timezone

from sqlmodel import Session

from app.ai import NoteMetadata, generate_note_metadata
from app.agent.graphs.note_metadata.state import NoteMetadataGraphState
from app.jobs.payloads import decode_payload
from app.models.job import Job
from app.models.note import Note


SessionFactory = Callable[[], AbstractContextManager[Session]]
MetadataGenerator = Callable[[str], NoteMetadata]


def build_load_note_node(session_factory: SessionFactory):
    def load_note(state: NoteMetadataGraphState) -> NoteMetadataGraphState:
        note_id = _resolve_note_id(state)
        expected_hash = state.get("content_hash") or ""
        with session_factory() as session:
            note = session.get(Note, note_id)
            if note is None:
                raise ValueError(f"Note {note_id} not found.")
            # job 绑定的是某个内容版本。用户修改/删除笔记后，旧 job 只允许跳过，
            # 不能再调用 LLM 或把旧 metadata 写回当前笔记。
            if note.status != "active" or (expected_hash and note.content_hash != expected_hash):
                return {"note_id": note_id, "content_hash": expected_hash, "should_skip": True}
            # 这里刻意保持幂等：恢复执行时，这个节点可能在 metadata 生成前再次运行。
            # 重复标记 processing 没有副作用，也能让前端状态保持真实。
            note.processing_status = "processing"
            note.processing_error = ""
            note.updated_at = utc_now()
            session.add(note)
            session.commit()
            return {
                "note_id": note_id,
                "content": note.content,
                "content_hash": note.content_hash,
                "should_skip": False,
            }

    return load_note


def build_generate_metadata_node(
    metadata_generator: MetadataGenerator = generate_note_metadata,
):
    def generate_metadata(state: NoteMetadataGraphState) -> NoteMetadataGraphState:
        if state.get("should_skip"):
            return {}
        content = state.get("content")
        if not content:
            raise ValueError("Note content is required before metadata generation.")
        metadata = metadata_generator(content)
        # 返回的 dict 会进入 checkpoint。若进程在此节点后中断，恢复时会继续
        # write_metadata，不会再次消耗 LLM 调用。
        return {"metadata": metadata.model_dump()}

    return generate_metadata


def build_write_metadata_node(session_factory: SessionFactory):
    def write_metadata(state: NoteMetadataGraphState) -> NoteMetadataGraphState:
        if state.get("should_skip"):
            return {}
        note_id = _resolve_note_id(state)
        expected_hash = state.get("content_hash") or ""
        raw_metadata = state.get("metadata")
        if not raw_metadata:
            raise ValueError("Metadata is required before writing note metadata.")
        metadata = NoteMetadata.model_validate(raw_metadata)

        with session_factory() as session:
            note = session.get(Note, note_id)
            if note is None:
                raise ValueError(f"Note {note_id} not found.")
            if note.status != "active" or (expected_hash and note.content_hash != expected_hash):
                return {}
            # 用户手动输入的标题是业务事实；AI 只能替换根据正文生成的 fallback 标题。
            if note.title_source != "user" and metadata.title:
                note.title = metadata.title
                note.title_source = "ai"
            # 覆盖写入，不追加。这样重试或 checkpoint 恢复时重复执行也不会产生重复数据。
            note.summary = metadata.summary
            note.tags = ",".join(metadata.tags)
            note.processing_status = "completed"
            note.processing_error = ""
            note.processed_at = utc_now()
            note.updated_at = utc_now()
            session.add(note)
            session.commit()
        return {}

    return write_metadata


def build_mark_failed_note(session_factory: SessionFactory):
    def mark_failed(job: Job, error: str) -> None:
        payload = decode_payload(job.payload)
        note_id = int(payload["note_id"])
        with session_factory() as session:
            note = session.get(Note, note_id)
            if note is None:
                return
            # job 失败状态和 note 处理状态分开记录：job 负责重试，note 负责用户可见状态。
            note.processing_status = "failed"
            note.processing_error = error[:4000]
            note.updated_at = utc_now()
            session.add(note)
            session.commit()

    return mark_failed


def _resolve_note_id(state: NoteMetadataGraphState) -> int:
    note_id = state.get("note_id")
    if note_id is None:
        raise ValueError("note_id is required.")
    return int(note_id)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)
