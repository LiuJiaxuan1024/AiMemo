import importlib.util
import sqlite3
from pathlib import Path
from struct import pack

from app.core.config import settings
from app.core.timing import elapsed_ms, emit_timing, now_counter


VECTOR_TABLE_NAME = "vec_note_chunks"


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


def upsert_chunk_embedding(chunk_id: int, embedding: list[float]) -> None:
    _validate_embedding(embedding)
    with connect_vector_store() as connection:
        # sqlite-vec 虚拟表没有业务层 upsert 语义，先删后插最直观；
        # rowid 固定为 chunk_id，保证业务表和向量表一一对应。
        connection.execute(f"DELETE FROM {VECTOR_TABLE_NAME} WHERE rowid = ?", (chunk_id,))
        connection.execute(
            f"INSERT INTO {VECTOR_TABLE_NAME}(rowid, embedding) VALUES (?, ?)",
            (chunk_id, serialize_float32(embedding)),
        )
        connection.commit()


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


def connect_vector_store():
    """创建已加载 sqlite-vec 扩展的 sqlite3 连接。

    SQLModel 使用的 engine 不能直接启用扩展；sqlite-vec 查询和写入使用单独连接。
    """

    database_path = get_sqlite_database_path()
    database_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(database_path)
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


def _sqlite_vec_extension_path() -> Path:
    spec = importlib.util.find_spec("sqlite_vec")
    if not spec or not spec.submodule_search_locations:
        raise RuntimeError("sqlite_vec package is required for vector storage.")
    return Path(next(iter(spec.submodule_search_locations))) / "vec0"
