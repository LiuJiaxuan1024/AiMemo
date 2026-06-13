from collections.abc import Callable
from contextlib import AbstractContextManager

from sqlmodel import Session

from app.jobs.models import JobType
from app.models.job import Job


JobHandler = Callable[[Job], None]
SessionFactory = Callable[[], AbstractContextManager[Session]]


def build_job_handlers(
    *,
    session_factory: SessionFactory,
    checkpoint_path: str,
) -> dict[str, JobHandler]:
    return {
        JobType.NOTE_METADATA.value: _build_note_metadata_handler(
            session_factory=session_factory,
            checkpoint_path=checkpoint_path,
        ),
        JobType.NOTE_EMBEDDING.value: _build_note_embedding_handler(
            session_factory=session_factory,
            checkpoint_path=checkpoint_path,
        ),
        JobType.KNOWLEDGE_INGEST.value: _build_knowledge_ingest_handler(
            session_factory=session_factory,
            checkpoint_path=checkpoint_path,
        ),
        JobType.KNOWLEDGE_IMAGE_RETRY.value: _build_knowledge_image_retry_handler(
            session_factory=session_factory,
        ),
        JobType.CONVERSATION_SUMMARY.value: _build_conversation_summary_handler(
            session_factory=session_factory,
            checkpoint_path=checkpoint_path,
        ),
        JobType.CONVERSATION_MEMORY.value: _build_conversation_memory_handler(
            session_factory=session_factory,
            checkpoint_path=checkpoint_path,
        ),
        JobType.CONVERSATION_TITLE.value: _build_conversation_title_handler(
            session_factory=session_factory,
            checkpoint_path=checkpoint_path,
        ),
    }


def _build_note_metadata_handler(
    *,
    session_factory: SessionFactory,
    checkpoint_path: str,
) -> JobHandler:
    def handle(job: Job) -> None:
        from app.agent.graphs.note_metadata.graph import run_note_metadata_graph

        run_note_metadata_graph(
            job,
            session_factory=session_factory,
            checkpoint_path=checkpoint_path,
        )

    return handle


def _build_note_embedding_handler(
    *,
    session_factory: SessionFactory,
    checkpoint_path: str,
) -> JobHandler:
    def handle(job: Job) -> None:
        from app.agent.graphs.note_embedding.graph import run_note_embedding_graph

        run_note_embedding_graph(
            job,
            session_factory=session_factory,
            checkpoint_path=checkpoint_path,
        )

    return handle


def _build_knowledge_ingest_handler(
    *,
    session_factory: SessionFactory,
    checkpoint_path: str,
) -> JobHandler:
    def handle(job: Job) -> None:
        from app.agent.graphs.knowledge_ingest.graph import run_knowledge_ingest_graph

        run_knowledge_ingest_graph(
            job,
            session_factory=session_factory,
            checkpoint_path=checkpoint_path,
        )

    return handle


def _build_knowledge_image_retry_handler(
    *,
    session_factory: SessionFactory,
) -> JobHandler:
    def handle(job: Job) -> None:
        from app.services.knowledge_image_asset_service import run_knowledge_image_retry_job

        run_knowledge_image_retry_job(job, session_factory=session_factory)

    return handle


def _build_conversation_summary_handler(
    *,
    session_factory: SessionFactory,
    checkpoint_path: str,
) -> JobHandler:
    def handle(job: Job) -> None:
        from app.agent.graphs.conversation_summary.graph import run_conversation_summary_graph

        run_conversation_summary_graph(
            job,
            session_factory=session_factory,
            checkpoint_path=checkpoint_path,
        )

    return handle


def _build_conversation_memory_handler(
    *,
    session_factory: SessionFactory,
    checkpoint_path: str,
) -> JobHandler:
    def handle(job: Job) -> None:
        from app.agent.graphs.conversation_memory.graph import run_conversation_memory_graph

        run_conversation_memory_graph(
            job,
            session_factory=session_factory,
            checkpoint_path=checkpoint_path,
        )

    return handle


def _build_conversation_title_handler(
    *,
    session_factory: SessionFactory,
    checkpoint_path: str,
) -> JobHandler:
    def handle(job: Job) -> None:
        from app.agent.graphs.conversation_title.graph import run_conversation_title_graph

        run_conversation_title_graph(
            job,
            session_factory=session_factory,
            checkpoint_path=checkpoint_path,
        )

    return handle
