from collections.abc import Generator
from contextlib import contextmanager
from datetime import datetime
import logging
import shutil
from pathlib import Path

from sqlalchemy import MetaData, inspect, text
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.schema import CreateTable
from sqlmodel import Session, SQLModel, create_engine

from app.core.config import settings
from app.models import (
    AgentOperation,
    BackgroundTask,
    ChatAttachment,
    ChatAttachmentDerivative,
    ChatMessage,
    ChatTurn,
    Conversation,
    ConversationKnowledgeMount,
    Job,
    KnowledgeChunk,
    KnowledgeDocument,
    KnowledgeSpace,
    LongTermMemory,
    Note,
    NoteChunk,
    VoiceProfile,
)
from app.rag.hashing import content_hash

logger = logging.getLogger(__name__)

# 启用 AUTOINCREMENT 的所有业务表。schema 升级时遍历这张清单做 PK rebuild。
# 用 model 对象本身（不是 tablename 字符串），rebuild 时直接拿 model.__table__
# 让 SQLAlchemy 编译出正确的 DDL，无需手写正则去 patch 旧 DDL（旧版做法把
# `PRIMARY KEY (id)` 分离约束当成 `id INTEGER PRIMARY KEY` inline 格式，全部 miss）。
_AUTOINCREMENT_MODELS = (
    Note,
    Conversation,
    ChatMessage,
    ChatAttachment,
    ChatAttachmentDerivative,
    ChatTurn,
    Job,
    KnowledgeSpace,
    KnowledgeDocument,
    KnowledgeChunk,
    ConversationKnowledgeMount,
    NoteChunk,
    LongTermMemory,
    AgentOperation,
    BackgroundTask,
    VoiceProfile,
)
_AUTOINCREMENT_TABLES = tuple(model.__tablename__ for model in _AUTOINCREMENT_MODELS)


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
    connect_args={"check_same_thread": False, "timeout": 30}
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
                "content_markdown": "TEXT DEFAULT ''",
                "content_blocks": "TEXT DEFAULT ''",
                "content_format": "VARCHAR(24) DEFAULT 'markdown'",
                "content_version": "INTEGER DEFAULT 1",
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
        _backfill_note_content_markdown(Note.__tablename__)
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

    if VoiceProfile.__tablename__ in table_names:
        _add_missing_columns(
            VoiceProfile.__tablename__,
            {
                "style_prompt": "TEXT DEFAULT ''",
                "preview_text": "TEXT DEFAULT ''",
                "language": "VARCHAR(24) DEFAULT 'auto'",
                "speed": "FLOAT DEFAULT 1.0",
                "energy": "FLOAT DEFAULT 1.0",
                "emotion_bias": "TEXT DEFAULT '{}'",
                "remote_target_model": "VARCHAR(120) DEFAULT ''",
                "last_error": "TEXT DEFAULT ''",
            },
        )

    _migrate_pk_to_autoincrement(table_names)


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


def _backfill_note_content_markdown(table_name: str) -> None:
    """为旧数据库补 Markdown 副本，保持 content 兼容字段不变。"""

    with engine.begin() as connection:
        connection.execute(
            text(
                f"""
                UPDATE {table_name}
                SET content_markdown = content
                WHERE content_markdown = '' OR content_markdown IS NULL
                """
            )
        )


# ─── SQLite PK AUTOINCREMENT 升级 ────────────────────────────────────
# SQLite 的 INTEGER PRIMARY KEY（不带 AUTOINCREMENT）会按 max(rowid)+1 分配 id——
# 删除最大 id 后下一个 INSERT 复用同样的 id。本块负责把已经存在的旧表 rebuild 为
# INTEGER PRIMARY KEY AUTOINCREMENT，让 id 严格单调递增、删除不复用。新部署的
# create_all 会通过 __table_args__ = {"sqlite_autoincrement": True} 直接得到正确 DDL，
# 不需要走这条路径。
#
# rebuild 流程（参考 SQLite 官方 "Making Other Kinds Of Table Schema Changes" 章节）：
#   1. 把 model.__table__ 复制到新 MetaData 取临时表名，用 SQLAlchemy 编译目标 DDL
#      —— 直接拿权威 schema 源，不再用脆弱的字符串 patch 旧 DDL。
#   2. PRAGMA foreign_keys=OFF，进 transaction
#   3. CREATE _new_<table>，INSERT SELECT，把 sqlite_sequence 设到当前 MAX(id)
#   4. DROP 旧表 → RENAME _new_<table> → 修 sqlite_sequence.name
#   5. 重建 indexes（DROP TABLE 会同时 drop indexes，提前从 sqlite_master 拿 sql）
#   6. PRAGMA foreign_keys=ON
# 幂等性：rebuild 前看 sqlite_master.sql 已含 AUTOINCREMENT 就 skip，重启不重复跑。


def _migrate_pk_to_autoincrement(table_names: list[str]) -> None:
    """对所有 _AUTOINCREMENT_MODELS 中已存在的表执行幂等 rebuild。"""

    if not settings.database_url.startswith("sqlite"):
        return
    targets = [model for model in _AUTOINCREMENT_MODELS if model.__tablename__ in table_names]
    if not targets:
        return
    pending = [model for model in targets if not _table_already_autoincrement(model.__tablename__)]
    if not pending:
        return
    backup_path = _backup_sqlite_before_autoincrement()
    if backup_path is not None:
        logger.info("sqlite autoincrement migration: backed up to %s", backup_path)
    for model in pending:
        try:
            _rebuild_pk_as_autoincrement(model)
            logger.info("sqlite autoincrement migration: rebuilt %s", model.__tablename__)
        except Exception:
            logger.exception(
                "sqlite autoincrement migration failed for %s; leaving original table intact",
                model.__tablename__,
            )


def _table_already_autoincrement(table_name: str) -> bool:
    with engine.begin() as connection:
        master_sql = connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:name"),
            {"name": table_name},
        ).scalar()
    return bool(master_sql) and "AUTOINCREMENT" in master_sql.upper()


def _backup_sqlite_before_autoincrement() -> Path | None:
    """在 rebuild 前把整库复制到一份带时间戳的备份文件，方便用户手工回滚。"""

    if not settings.database_url.startswith("sqlite:///"):
        return None
    db_path = Path(settings.database_url.replace("sqlite:///", "", 1))
    if not db_path.exists():
        return None
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    backup_path = db_path.with_name(f"{db_path.name}.pre-autoincrement-{timestamp}")
    try:
        shutil.copy2(db_path, backup_path)
    except Exception:
        logger.exception("failed to back up sqlite db before autoincrement migration")
        return None
    return backup_path


def _compile_temp_table_ddl(model, temp_name: str) -> str:
    """让 SQLAlchemy 编译出"和 model 同 schema、但表名换成 temp_name"的 CREATE TABLE。

    复制到一份独立 MetaData，不污染全局 SQLModel.metadata。CreateTable 只输出
    CREATE TABLE 语句本身——索引由我们随后从旧表的 sqlite_master 里捞出来再重建。
    """
    new_meta = MetaData()
    # SQLAlchemy 1.4+ 的方法叫 to_metadata；老名字 tometadata 仍是别名。
    to_meta = getattr(model.__table__, "to_metadata", None) or model.__table__.tometadata
    temp_table = to_meta(new_meta, name=temp_name)
    return str(CreateTable(temp_table).compile(dialect=sqlite_dialect.dialect()))


def _rebuild_pk_as_autoincrement(model) -> None:
    """把单张表 rebuild 为 INTEGER PRIMARY KEY AUTOINCREMENT。"""

    table_name = model.__tablename__
    with engine.begin() as connection:
        master_sql = connection.execute(
            text("SELECT sql FROM sqlite_master WHERE type='table' AND name=:name"),
            {"name": table_name},
        ).scalar()
        if not master_sql:
            return
        if "AUTOINCREMENT" in master_sql.upper():
            return  # 已经升级过，幂等跳过

        temp_name = f"_new_{table_name}"
        # 1) 直接让 SQLAlchemy 用当前 model（已带 sqlite_autoincrement=True）编译 DDL
        new_ddl = _compile_temp_table_ddl(model, temp_name)
        if "AUTOINCREMENT" not in new_ddl.upper():
            logger.error(
                "sqlite autoincrement migration: SQLAlchemy-compiled DDL for %s lacks AUTOINCREMENT; "
                "is __table_args__ = {'sqlite_autoincrement': True} set on the model?",
                table_name,
            )
            return

        # 2) 提前拿出 indexes（DROP TABLE 会一起删）
        index_rows = connection.execute(
            text(
                "SELECT sql FROM sqlite_master "
                "WHERE type='index' AND tbl_name=:name AND sql IS NOT NULL"
            ),
            {"name": table_name},
        ).fetchall()

        # 3) 拿列顺序（按 PRAGMA table_info），用于 INSERT SELECT。
        #    用旧表的列名 + 新表的列名取交集，防止"模型新增了列、旧表还没回填"时炸列。
        col_rows = connection.execute(text(f'PRAGMA table_info("{table_name}")')).fetchall()
        old_col_names = [row[1] for row in col_rows]
        new_col_names = {c.name for c in model.__table__.columns}
        shared = [c for c in old_col_names if c in new_col_names]
        col_list = ", ".join(f'"{c}"' for c in shared)

        # 4) 关 FK + 执行 rebuild
        connection.execute(text("PRAGMA foreign_keys = OFF"))
        try:
            connection.execute(text(new_ddl))
            connection.execute(
                text(
                    f'INSERT INTO "{temp_name}" ({col_list}) '
                    f'SELECT {col_list} FROM "{table_name}"'
                )
            )
            # 把 sqlite_sequence 设到当前 MAX(id)，确保下一个 INSERT 从 MAX(id)+1 开始。
            max_id = connection.execute(text(f'SELECT MAX(id) FROM "{temp_name}"')).scalar() or 0
            try:
                connection.execute(
                    text(
                        "INSERT INTO sqlite_sequence (name, seq) VALUES (:n, :s) "
                        "ON CONFLICT(name) DO UPDATE SET seq=excluded.seq"
                    ),
                    {"n": temp_name, "s": int(max_id)},
                )
            except Exception:
                # 部分老版本 sqlite 不支持 ON CONFLICT；退化为 DELETE + INSERT。
                connection.execute(
                    text("DELETE FROM sqlite_sequence WHERE name=:n"),
                    {"n": temp_name},
                )
                connection.execute(
                    text("INSERT INTO sqlite_sequence (name, seq) VALUES (:n, :s)"),
                    {"n": temp_name, "s": int(max_id)},
                )
            connection.execute(text(f'DROP TABLE "{table_name}"'))
            connection.execute(text(f'ALTER TABLE "{temp_name}" RENAME TO "{table_name}"'))
            # rename 后 sqlite_sequence 里的 name 字段还指向临时名，同步修正。
            connection.execute(
                text("UPDATE sqlite_sequence SET name=:new WHERE name=:old"),
                {"new": table_name, "old": temp_name},
            )
            # 5) 重建 indexes（DROP TABLE 已经把它们删了）。旧 DDL 里写的是原表名，
            #    rename 后表名是同一个，所以索引会自动挂到新表上。
            for (idx_sql,) in index_rows:
                connection.execute(text(idx_sql))
        finally:
            connection.execute(text("PRAGMA foreign_keys = ON"))
