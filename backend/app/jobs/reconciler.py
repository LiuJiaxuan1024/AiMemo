import logging
import threading
from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass

from sqlmodel import Session, col, select

from app.core.config import settings
from app.core.database import session_scope
from app.jobs.models import GraphName, JobType
from app.jobs.queue import ACTIVE_STATUSES, enqueue_job
from app.services.conversation_summary_service import conversation_needs_summary
from app.services.long_term_memory_service import enqueue_conversation_memory_job_if_needed
from app.models.chat_message import ChatMessage
from app.models.conversation import Conversation
from app.models.job import Job
from app.models.note import Note


logger = logging.getLogger(__name__)

SessionFactory = Callable[[], AbstractContextManager[Session]]


@dataclass(frozen=True)
class ReconcileResult:
    metadata_jobs_created: int = 0
    embedding_jobs_created: int = 0
    summary_jobs_created: int = 0
    memory_jobs_created: int = 0

    @property
    def total_jobs_created(self) -> int:
        return (
            self.metadata_jobs_created
            + self.embedding_jobs_created
            + self.summary_jobs_created
            + self.memory_jobs_created
        )


def reconcile_missing_jobs(session: Session) -> ReconcileResult:
    """修复“业务状态需要后台任务，但 jobs 表里没有活跃任务”的不一致。

    这层逻辑刻意不绑定到 HTTP 或 worker 线程，方便后续加入更多规则：
    例如全文索引、摘要重建、附件解析、向量重建等状态都可以在这里补 job。
    """

    metadata_count = _enqueue_note_metadata_jobs(session)
    embedding_count = _enqueue_note_embedding_jobs(session)
    summary_count = _enqueue_conversation_summary_jobs(session)
    memory_count = _enqueue_conversation_memory_jobs(session)
    session.commit()
    return ReconcileResult(
        metadata_jobs_created=metadata_count,
        embedding_jobs_created=embedding_count,
        summary_jobs_created=summary_count,
        memory_jobs_created=memory_count,
    )


def _enqueue_note_metadata_jobs(session: Session) -> int:
    notes = session.exec(
        select(Note).where(col(Note.processing_status).in_(["pending", "processing"]))
    ).all()
    created = 0
    for note in notes:
        if note.id is None:
            continue
        dedupe_key = f"{JobType.NOTE_METADATA.value}:note:{note.id}"
        if _has_active_job(session, dedupe_key):
            continue
        job = enqueue_job(
            session,
            job_type=JobType.NOTE_METADATA.value,
            graph_name=GraphName.NOTE_METADATA.value,
            payload={"note_id": note.id},
            dedupe_key=dedupe_key,
        )
        if job.id is not None:
            created += 1
    return created


def _enqueue_note_embedding_jobs(session: Session) -> int:
    notes = session.exec(
        select(Note).where(col(Note.embedding_status).in_(["pending", "processing"]))
    ).all()
    created = 0
    for note in notes:
        if note.id is None:
            continue
        dedupe_key = f"{JobType.NOTE_EMBEDDING.value}:note:{note.id}"
        if _has_active_job(session, dedupe_key):
            continue
        job = enqueue_job(
            session,
            job_type=JobType.NOTE_EMBEDDING.value,
            graph_name=GraphName.NOTE_EMBEDDING.value,
            payload={"note_id": note.id},
            dedupe_key=dedupe_key,
        )
        if job.id is not None:
            created += 1
    return created


def _enqueue_conversation_summary_jobs(session: Session) -> int:
    conversations = session.exec(
        select(Conversation).where(Conversation.status == "active")
    ).all()
    created = 0
    for conversation in conversations:
        if conversation.id is None:
            continue
        if not conversation_needs_summary(session, conversation):
            continue
        dedupe_key = f"{JobType.CONVERSATION_SUMMARY.value}:conversation:{conversation.id}"
        if _has_active_job(session, dedupe_key):
            continue
        job = enqueue_job(
            session,
            job_type=JobType.CONVERSATION_SUMMARY.value,
            graph_name=GraphName.CONVERSATION_SUMMARY.value,
            payload={"conversation_id": conversation.id},
            dedupe_key=dedupe_key,
        )
        if job.id is not None:
            created += 1
    return created


def _enqueue_conversation_memory_jobs(session: Session) -> int:
    """补建缺失的长期记忆抽取任务。

    这里按 assistant 消息扫描，因为一轮聊天完成后 assistant_message_id 是稳定边界。
    如果该 assistant 消息已经有任何状态的 dedupe job，就不再补建，避免“无可写记忆”
    的消息被周期性重复抽取。
    """

    assistant_messages = session.exec(
        select(ChatMessage)
        .where(ChatMessage.role == "assistant")
        .where(ChatMessage.status == "completed")
    ).all()
    created = 0
    for assistant in assistant_messages:
        if assistant.id is None or assistant.parent_id is None:
            continue
        job = enqueue_conversation_memory_job_if_needed(
            session,
            conversation_id=assistant.conversation_id,
            user_message_id=assistant.parent_id,
            assistant_message_id=assistant.id,
        )
        if job is not None and job.id is not None:
            created += 1
    return created


def _has_active_job(session: Session, dedupe_key: str) -> bool:
    job = session.exec(
        select(Job).where(
            Job.dedupe_key == dedupe_key,
            col(Job.status).in_(ACTIVE_STATUSES),
        )
    ).first()
    return job is not None


class JobReconciler:
    """周期性检查业务状态和 job 队列的一致性。

    worker 负责“执行已经存在的 job”，reconciler 负责“发现应该存在但缺失的 job”。
    二者分开后，后续增加更多业务状态检查时，不会污染 worker 的领取和执行逻辑。
    """

    def __init__(
        self,
        *,
        session_factory: SessionFactory,
        interval_seconds: float,
    ) -> None:
        self.session_factory = session_factory
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._thread = threading.Thread(target=self.run_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop_event.set()
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=5)

    def run_forever(self) -> None:
        while not self._stop_event.is_set():
            self.run_once()
            self._stop_event.wait(self.interval_seconds)

    def run_once(self) -> ReconcileResult:
        with self.session_factory() as session:
            result = reconcile_missing_jobs(session)
        if result.total_jobs_created:
            logger.info("Reconciler created %s missing jobs.", result.total_jobs_created)
        return result


_reconciler: JobReconciler | None = None


def run_job_reconcile_once() -> ReconcileResult:
    with session_scope() as session:
        return reconcile_missing_jobs(session)


def start_job_reconciler() -> None:
    global _reconciler
    if not settings.job_reconciler_enabled:
        return
    if _reconciler is not None:
        return
    _reconciler = JobReconciler(
        session_factory=session_scope,
        interval_seconds=settings.job_reconciler_interval_seconds,
    )
    _reconciler.start()


def stop_job_reconciler() -> None:
    global _reconciler
    if _reconciler is None:
        return
    _reconciler.stop()
    _reconciler = None
