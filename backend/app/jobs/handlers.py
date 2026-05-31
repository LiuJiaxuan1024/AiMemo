from collections.abc import Callable
from contextlib import AbstractContextManager

from sqlmodel import Session

from app.agent.graphs.conversation_memory.graph import run_conversation_memory_graph
from app.agent.graphs.conversation_summary.graph import run_conversation_summary_graph
from app.agent.graphs.conversation_title.graph import run_conversation_title_graph
from app.agent.graphs.knowledge_ingest.graph import run_knowledge_ingest_graph
from app.agent.graphs.note_embedding.graph import run_note_embedding_graph
from app.agent.graphs.note_metadata.graph import run_note_metadata_graph
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
        JobType.NOTE_METADATA.value: lambda job: run_note_metadata_graph(
            job,
            session_factory=session_factory,
            checkpoint_path=checkpoint_path,
        ),
        JobType.NOTE_EMBEDDING.value: lambda job: run_note_embedding_graph(
            job,
            session_factory=session_factory,
            checkpoint_path=checkpoint_path,
        ),
        JobType.KNOWLEDGE_INGEST.value: lambda job: run_knowledge_ingest_graph(
            job,
            session_factory=session_factory,
            checkpoint_path=checkpoint_path,
        ),
        JobType.CONVERSATION_SUMMARY.value: lambda job: run_conversation_summary_graph(
            job,
            session_factory=session_factory,
            checkpoint_path=checkpoint_path,
        ),
        JobType.CONVERSATION_MEMORY.value: lambda job: run_conversation_memory_graph(
            job,
            session_factory=session_factory,
            checkpoint_path=checkpoint_path,
        ),
        JobType.CONVERSATION_TITLE.value: lambda job: run_conversation_title_graph(
            job,
            session_factory=session_factory,
            checkpoint_path=checkpoint_path,
        ),
    }
