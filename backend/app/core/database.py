from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import inspect, text
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import settings
from app.models import AgentOperation, ChatMessage, ChatTurn, Conversation, Job, LongTermMemory, Note, NoteChunk
from app.rag.hashing import content_hash


def _ensure_sqlite_parent_dir(database_url: str) -> None:
    if not database_url.startswith("sqlite:///"):
        return

    sqlite_path = database_url.replace("sqlite:///", "", 1)
    parent = Path(sqlite_path).parent
    if str(parent) != ".":
        parent.mkdir(parents=True, exist_ok=True)


_ensure_sqlite_parent_dir(settings.database_url)

engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False}
    if settings.database_url.startswith("sqlite")
    else {},
)


def create_db_and_tables() -> None:
    SQLModel.metadata.create_all(engine)
    migrate_existing_sqlite_schema()


def get_session() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


@contextmanager
def session_scope() -> Generator[Session, None, None]:
    with Session(engine) as session:
        yield session


def migrate_existing_sqlite_schema() -> None:
    if not settings.database_url.startswith("sqlite"):
        return

    inspector = inspect(engine)
    table_names = inspector.get_table_names()
    if Note.__tablename__ in table_names:
        _add_missing_columns(
            Note.__tablename__,
            {
                "title_source": "VARCHAR(24) DEFAULT 'fallback'",
                "content_hash": "VARCHAR(64) DEFAULT ''",
                "status": "VARCHAR(24) DEFAULT 'active'",
                "processing_status": "VARCHAR(24) DEFAULT 'completed'",
                "processing_error": "TEXT DEFAULT ''",
                "processed_at": "DATETIME",
                "embedding_status": "VARCHAR(24) DEFAULT 'pending'",
                "embedding_error": "TEXT DEFAULT ''",
                "embedded_at": "DATETIME",
                "deleted_at": "DATETIME",
            },
        )
        _backfill_note_content_hash(Note.__tablename__)

    if Job.__tablename__ in table_names:
        _add_missing_columns(
            Job.__tablename__,
            {
                "graph_name": "VARCHAR(120)",
                "thread_id": "VARCHAR(120)",
                "dedupe_key": "VARCHAR(200)",
            },
        )

    if Conversation.__tablename__ in table_names:
        _add_missing_columns(
            Conversation.__tablename__,
            {
                "summary": "TEXT DEFAULT ''",
                "summary_message_id": "INTEGER",
                "active_task": "TEXT DEFAULT '{}'",
                "langgraph_thread_id": "VARCHAR(120) DEFAULT ''",
            },
        )

    if NoteChunk.__tablename__ in table_names:
        _add_missing_columns(
            NoteChunk.__tablename__,
            {
                "embedding_status": "VARCHAR(24) DEFAULT 'pending'",
                "embedding_error": "TEXT DEFAULT ''",
            },
        )

    if LongTermMemory.__tablename__ in table_names:
        _add_missing_columns(
            LongTermMemory.__tablename__,
            {
                "category": "VARCHAR(40) DEFAULT 'fact'",
                "memory_key": "VARCHAR(120) DEFAULT ''",
                "summary": "TEXT DEFAULT ''",
            },
        )

    if ChatTurn.__tablename__ in table_names:
        _add_missing_columns(
            ChatTurn.__tablename__,
            {
                "debug_payload": "TEXT DEFAULT '{}'",
            },
        )


def _add_missing_columns(table_name: str, columns: dict[str, str]) -> None:
    inspector = inspect(engine)
    existing_columns = {column["name"] for column in inspector.get_columns(table_name)}
    with engine.begin() as connection:
        for column_name, column_sql in columns.items():
            if column_name not in existing_columns:
                connection.execute(
                    text(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_sql}")
                )


def _backfill_note_content_hash(table_name: str) -> None:
    """为旧数据库中的笔记补 content_hash。

    SQLite 没有内置 sha256，这里用应用层 hash 回填，保证旧笔记也能参与新版 job 去重。
    """

    with engine.begin() as connection:
        rows = connection.execute(
            text(f"SELECT id, content FROM {table_name} WHERE content_hash = '' OR content_hash IS NULL")
        ).fetchall()
        for note_id, content in rows:
            connection.execute(
                text(f"UPDATE {table_name} SET content_hash = :hash WHERE id = :id"),
                {"hash": content_hash(str(content or "").strip()), "id": note_id},
            )
