import importlib.util
import sqlite3
from pathlib import Path
from struct import pack

from app.core.config import settings
from app.core.timing import elapsed_ms, emit_timing, now_counter


VECTOR_TABLE_NAME = "vec_note_chunks"
KNOWLEDGE_VECTOR_TABLE_NAME = "vec_knowledge_chunks"
SQLITE_BUSY_TIMEOUT_MS = 30_000


def get_sqlite_database_path() -> Path:
    """从 SQLModel 的 sqlite URL 中解析 sqlite-vec 使用的物理数据库路径。"""

    if not settings.database_url.startswith("sqlite:///"):
        raise RuntimeError("sqlite-vec vector store requires a sqlite database URL.")
    return Path(settings.database_url.replace("sqlite:///", "", 1))


def ensure_vector_store() -> None:
    """确保向量虚拟表存在。

    业务 chunk 存在 notechunk 表；embedding 只存在 sqlite-vec 虚拟表。
    二者通过 rowid == notechunk.id 建立关系。
    """

    with connect_vector_store() as connection:
        connection.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {VECTOR_TABLE_NAME} "
            f"USING vec0(embedding float[{settings.embedding_dimensions}])"
        )
        connection.commit()


def ensure_knowledge_vector_store() -> None:
    """Ensure the knowledge chunk vector table exists."""

    with connect_vector_store() as connection:
        connection.execute(
            f"CREATE VIRTUAL TABLE IF NOT EXISTS {KNOWLEDGE_VECTOR_TABLE_NAME} "
            f"USING vec0(embedding float[{settings.embedding_dimensions}])"
        )
        connection.commit()


def upsert_chunk_embedding(chunk_id: int, embedding: list[float]) -> None:
    upsert_chunk_embeddings([(chunk_id, embedding)])


def upsert_chunk_embeddings(items: list[tuple[int, list[float]]]) -> None:
    if not items:
        return
    for _, embedding in items:
        _validate_embedding(embedding)
    ensure_vector_store()
    with connect_vector_store() as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.executemany(
                f"DELETE FROM {VECTOR_TABLE_NAME} WHERE rowid = ?",
                [(chunk_id,) for chunk_id, _ in items],
            )
            connection.executemany(
                f"INSERT INTO {VECTOR_TABLE_NAME}(rowid, embedding) VALUES (?, ?)",
                [(chunk_id, serialize_float32(embedding)) for chunk_id, embedding in items],
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def upsert_knowledge_chunk_embeddings(items: list[tuple[int, list[float]]]) -> None:
    if not items:
        return
    for _, embedding in items:
        _validate_embedding(embedding)
    ensure_knowledge_vector_store()
    with connect_vector_store() as connection:
        connection.execute("BEGIN IMMEDIATE")
        try:
            connection.executemany(
                f"DELETE FROM {KNOWLEDGE_VECTOR_TABLE_NAME} WHERE rowid = ?",
                [(chunk_id,) for chunk_id, _ in items],
            )
            connection.executemany(
                f"INSERT INTO {KNOWLEDGE_VECTOR_TABLE_NAME}(rowid, embedding) VALUES (?, ?)",
                [(chunk_id, serialize_float32(embedding)) for chunk_id, embedding in items],
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise


def upsert_knowledge_chunk_embedding(chunk_id: int, embedding: list[float]) -> None:
    upsert_knowledge_chunk_embeddings([(chunk_id, embedding)])


def delete_note_chunk_embeddings(chunk_ids: list[int]) -> None:
    """删除 chunk 对应的向量。

    删除是幂等清理动作：测试环境或首次启动时向量表可能还没创建，此时先确保表存在，
    避免“没有可删内容”反过来打断笔记修改/永久删除流程。
    """

    if not chunk_ids:
        return
    ensure_vector_store()
    with connect_vector_store() as connection:
        connection.executemany(
            f"DELETE FROM {VECTOR_TABLE_NAME} WHERE rowid = ?",
            [(chunk_id,) for chunk_id in chunk_ids],
        )
        connection.commit()


def delete_knowledge_chunk_embeddings(chunk_ids: list[int]) -> None:
    if not chunk_ids:
        return
    ensure_knowledge_vector_store()
    with connect_vector_store() as connection:
        connection.executemany(
            f"DELETE FROM {KNOWLEDGE_VECTOR_TABLE_NAME} WHERE rowid = ?",
            [(chunk_id,) for chunk_id in chunk_ids],
        )
        connection.commit()


def search_chunk_embeddings(query_embedding: list[float], *, limit: int) -> list[tuple[int, float]]:
    """返回与查询向量最相近的 chunk id 和距离。

    这里不读取 notechunk/note 业务表，保持 vector_store 只负责向量索引。
    """

    total_started_at = now_counter()
    validate_started_at = now_counter()
    _validate_embedding(query_embedding)
    validate_ms = elapsed_ms(validate_started_at)
    if limit <= 0:
        return []
    ensure_started_at = now_counter()
    ensure_vector_store()
    ensure_ms = elapsed_ms(ensure_started_at)
    connect_started_at = now_counter()
    with connect_vector_store() as connection:
        connect_ms = elapsed_ms(connect_started_at)
        serialize_started_at = now_counter()
        serialized_embedding = serialize_float32(query_embedding)
        serialize_ms = elapsed_ms(serialize_started_at)
        query_started_at = now_counter()
        # sqlite-vec 使用 MATCH 做近邻检索，rowid 与 notechunk.id 保持一致。
        rows = connection.execute(
            f"SELECT rowid, distance FROM {VECTOR_TABLE_NAME} "
            "WHERE embedding MATCH ? AND k = ? "
            "ORDER BY distance",
            (serialized_embedding, limit),
        ).fetchall()
        query_ms = elapsed_ms(query_started_at)
    result_started_at = now_counter()
    result = [(int(row[0]), float(row[1])) for row in rows]
    result_ms = elapsed_ms(result_started_at)
    emit_timing(
        "rag.vector_search_timing",
        total_ms=elapsed_ms(total_started_at),
        validate_ms=validate_ms,
        ensure_ms=ensure_ms,
        connect_ms=connect_ms,
        serialize_ms=serialize_ms,
        query_ms=query_ms,
        result_ms=result_ms,
        limit=limit,
        result_count=len(result),
    )
    return result


def search_knowledge_chunk_embeddings(query_embedding: list[float], *, limit: int) -> list[tuple[int, float]]:
    """Return nearest knowledge chunk ids and distances from vec_knowledge_chunks."""

    _validate_embedding(query_embedding)
    if limit <= 0:
        return []
    ensure_knowledge_vector_store()
    with connect_vector_store() as connection:
        serialized_embedding = serialize_float32(query_embedding)
        rows = connection.execute(
            f"SELECT rowid, distance FROM {KNOWLEDGE_VECTOR_TABLE_NAME} "
            "WHERE embedding MATCH ? AND k = ? "
            "ORDER BY distance",
            (serialized_embedding, limit),
        ).fetchall()
    return [(int(row[0]), float(row[1])) for row in rows]


def connect_vector_store():
    """创建已加载 sqlite-vec 扩展的 sqlite3 连接。

    SQLModel 使用的 engine 不能直接启用扩展；sqlite-vec 查询和写入使用单独连接。
    """

    database_path = get_sqlite_database_path()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path, timeout=SQLITE_BUSY_TIMEOUT_MS / 1000)
    connection.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    _enable_wal_if_possible(connection)
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.enable_load_extension(True)
    connection.load_extension(str(_sqlite_vec_extension_path()))
    connection.enable_load_extension(False)
    return connection


def serialize_float32(vector: list[float]) -> bytes:
    """sqlite-vec 接收 float32 二进制 blob，这里把 Python float list 打包为 float32。"""

    return pack(f"{len(vector)}f", *vector)


def _validate_embedding(embedding: list[float]) -> None:
    if len(embedding) != settings.embedding_dimensions:
        raise ValueError(
            f"Embedding dimension mismatch: expected {settings.embedding_dimensions}, "
            f"got {len(embedding)}."
        )


def _enable_wal_if_possible(connection: sqlite3.Connection) -> None:
    try:
        connection.execute("PRAGMA journal_mode = WAL")
    except sqlite3.OperationalError:
        # WAL is an optimization here. If another process is holding a lock,
        # keep the connection usable and let busy_timeout handle real writes.
        pass


def _sqlite_vec_extension_path() -> Path:
    spec = importlib.util.find_spec("sqlite_vec")
    if not spec or not spec.submodule_search_locations:
        raise RuntimeError("sqlite_vec package is required for vector storage.")
    return Path(next(iter(spec.submodule_search_locations))) / "vec0"
