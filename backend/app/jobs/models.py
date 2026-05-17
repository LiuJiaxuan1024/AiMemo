from enum import StrEnum


class JobStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELED = "canceled"


class JobType(StrEnum):
    NOTE_METADATA = "note_metadata"
    NOTE_EMBEDDING = "note_embedding"
    CONVERSATION_SUMMARY = "conversation_summary"
    CONVERSATION_MEMORY = "conversation_memory"


class GraphName(StrEnum):
    NOTE_METADATA = "note_metadata_graph"
    NOTE_EMBEDDING = "note_embedding_graph"
    CONVERSATION_SUMMARY = "conversation_summary_graph"
    CONVERSATION_MEMORY = "conversation_memory_graph"
