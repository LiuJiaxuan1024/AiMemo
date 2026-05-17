from app.agent.graphs.registry import get_job_graph_view
from app.jobs.models import GraphName, JobType
from app.jobs.queue import enqueue_job
from app.services.job_service import get_job_graph, list_jobs


def test_list_jobs_returns_decoded_payload(session):
    job = enqueue_job(
        session,
        job_type=JobType.NOTE_METADATA.value,
        graph_name=GraphName.NOTE_METADATA.value,
        payload={"note_id": 123},
    )
    session.commit()

    jobs = list_jobs(session)

    assert jobs[0].id == job.id
    assert jobs[0].payload == {"note_id": 123}


def test_job_graph_view_returns_mermaid(session):
    job = enqueue_job(
        session,
        job_type=JobType.NOTE_METADATA.value,
        graph_name=GraphName.NOTE_METADATA.value,
        payload={"note_id": 123},
    )
    session.commit()
    session.refresh(job)

    graph = get_job_graph(session, job.id or 0)

    assert graph.graph_name == GraphName.NOTE_METADATA.value
    assert "load_note" in graph.mermaid
    assert "generate_metadata" in graph.mermaid
    assert "write_metadata" in graph.mermaid


def test_summary_job_graph_view_returns_mermaid(session):
    job = enqueue_job(
        session,
        job_type=JobType.CONVERSATION_SUMMARY.value,
        graph_name=GraphName.CONVERSATION_SUMMARY.value,
        payload={"conversation_id": 1},
    )
    session.commit()
    session.refresh(job)

    graph = get_job_graph(session, job.id or 0)

    assert graph.graph_name == GraphName.CONVERSATION_SUMMARY.value
    assert "load_summary_inputs" in graph.mermaid
    assert "summarize_messages" in graph.mermaid
    assert "persist_summary" in graph.mermaid


def test_memory_job_graph_view_returns_mermaid(session):
    job = enqueue_job(
        session,
        job_type=JobType.CONVERSATION_MEMORY.value,
        graph_name=GraphName.CONVERSATION_MEMORY.value,
        payload={
            "conversation_id": 1,
            "user_message_id": 1,
            "assistant_message_id": 2,
        },
    )
    session.commit()
    session.refresh(job)

    graph = get_job_graph(session, job.id or 0)

    assert graph.graph_name == GraphName.CONVERSATION_MEMORY.value
    assert "load_memory_source" in graph.mermaid
    assert "extract_memories" in graph.mermaid
    assert "write_memories" in graph.mermaid
