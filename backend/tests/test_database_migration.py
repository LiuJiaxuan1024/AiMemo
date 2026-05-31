"""测试 SQLite PK AUTOINCREMENT 升级路径。

新部署（conftest.py 的 fresh test_engine）由 SQLModel.metadata.create_all 直接发出
带 AUTOINCREMENT 的 DDL（因为 model 上有 __table_args__ = {"sqlite_autoincrement": True}），
不需要走 rebuild。本测试专门覆盖"已有非 AUTOINCREMENT 的旧表"→ 跑 rebuild → 数据 +
DDL 都正确这条路径，并验证幂等。

关键：legacy 表用**真实 SQLAlchemy 生成的 DDL**（去掉 AUTOINCREMENT 模拟旧 schema），
不是手工拼接的 inline-PK 格式——避免 v1 实现中"测试通过、生产 0 匹配"的陷阱。
"""
from __future__ import annotations

from pathlib import Path

from sqlalchemy import text
from sqlalchemy.dialects import sqlite as sqlite_dialect
from sqlalchemy.schema import CreateTable
from sqlmodel import create_engine

from app.core import database as db_module
from app.models import ChatTurn


def _legacy_chat_turn_ddl() -> str:
    """生成与生产存量库一致的 chat_turn DDL：SQLAlchemy 标准格式 + 去掉 AUTOINCREMENT。"""
    ddl = str(CreateTable(ChatTurn.__table__).compile(dialect=sqlite_dialect.dialect()))
    # 把 AUTOINCREMENT 关键字去掉，模拟"加 __table_args__ 之前的 DDL"。
    return ddl.replace(" AUTOINCREMENT", "")


def _make_legacy_engine(tmp_path: Path):
    """造一个含旧式（无 AUTOINCREMENT）chat_turn 表的临时 SQLite，模拟存量库现场。"""
    db_path = tmp_path / "legacy.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    with engine.begin() as conn:
        conn.execute(text(_legacy_chat_turn_ddl()))
        conn.execute(
            text(
                'CREATE INDEX "ix_chat_turn_conversation_id" '
                'ON "chatturn" ("conversation_id")'
            )
        )
        # ChatTurn 真实 schema 里 node_statuses / context_layers / retrieved_chunks /
        # debug_payload / error / created_at / updated_at 都是 NOT NULL；测试里直接
        # 给最小可写入值，避免不相关字段干扰 PK rebuild 行为本身。
        for tid, cid in [(1, 10), (2, 10), (3, 11)]:
            conn.execute(
                text(
                    'INSERT INTO "chatturn" (id, conversation_id, thread_id, status, '
                    "node_statuses, context_layers, retrieved_chunks, debug_payload, "
                    "error, created_at, updated_at) "
                    "VALUES (:tid, :cid, :tname, 'completed', '{}', '{}', '[]', "
                    "'{}', '', '2026-01-01', '2026-01-01')"
                ),
                {"tid": tid, "cid": cid, "tname": f"conversation:{cid}"},
            )
    return engine, db_path


def test_rebuild_legacy_table_adds_autoincrement(tmp_path, monkeypatch):
    engine, _ = _make_legacy_engine(tmp_path)
    monkeypatch.setattr(db_module, "engine", engine)

    # rebuild 前：sqlite_master 不含 AUTOINCREMENT
    assert not db_module._table_already_autoincrement("chatturn")

    db_module._rebuild_pk_as_autoincrement(ChatTurn)

    # rebuild 后：DDL 含 AUTOINCREMENT
    assert db_module._table_already_autoincrement("chatturn")

    # 数据完整保留
    with engine.begin() as conn:
        rows = conn.execute(
            text('SELECT id, conversation_id, thread_id FROM "chatturn" ORDER BY id')
        ).fetchall()
    assert [tuple(row) for row in rows] == [
        (1, 10, "conversation:10"),
        (2, 10, "conversation:10"),
        (3, 11, "conversation:11"),
    ]

    # index 重建成功
    with engine.begin() as conn:
        idx_rows = conn.execute(
            text("SELECT name FROM sqlite_master WHERE type='index' AND tbl_name='chatturn'")
        ).fetchall()
    assert any(row[0] == "ix_chat_turn_conversation_id" for row in idx_rows)


def test_rebuild_is_idempotent(tmp_path, monkeypatch):
    engine, _ = _make_legacy_engine(tmp_path)
    monkeypatch.setattr(db_module, "engine", engine)

    db_module._rebuild_pk_as_autoincrement(ChatTurn)
    # 二次跑不应抛错也不应破坏数据；幂等检查会直接 return。
    db_module._rebuild_pk_as_autoincrement(ChatTurn)

    with engine.begin() as conn:
        rows = conn.execute(text('SELECT COUNT(*) FROM "chatturn"')).scalar()
    assert rows == 3


def test_rebuild_prevents_id_reuse_after_delete_max(tmp_path, monkeypatch):
    """核心回归：rebuild 之后，删除 max id 行不会让下一个 INSERT 复用 id。"""
    engine, _ = _make_legacy_engine(tmp_path)
    monkeypatch.setattr(db_module, "engine", engine)

    db_module._rebuild_pk_as_autoincrement(ChatTurn)

    with engine.begin() as conn:
        conn.execute(text('DELETE FROM "chatturn" WHERE id = 3'))
        conn.execute(
            text(
                'INSERT INTO "chatturn" (conversation_id, thread_id, status, '
                "node_statuses, context_layers, retrieved_chunks, debug_payload, "
                "error, created_at, updated_at) "
                "VALUES (99, 'conversation:99', 'completed', '{}', '{}', '[]', "
                "'{}', '', '2026-01-01', '2026-01-01')"
            )
        )
        new_id = conn.execute(
            text('SELECT id FROM "chatturn" WHERE conversation_id = 99')
        ).scalar()

    # 旧行为：删了 max(id)=3 之后 INSERT 会得 id=3（复用）
    # 新行为：AUTOINCREMENT 走 sqlite_sequence，得 id=4，永不复用。
    assert new_id == 4


def test_migrate_pk_to_autoincrement_skips_when_nothing_to_do(tmp_path, monkeypatch):
    """所有表都已 AUTOINCREMENT 时入口函数应该 no-op，不会触发备份。"""
    engine, _ = _make_legacy_engine(tmp_path)
    monkeypatch.setattr(db_module, "engine", engine)
    db_module._rebuild_pk_as_autoincrement(ChatTurn)

    backup_calls: list[Path] = []
    monkeypatch.setattr(
        db_module,
        "_backup_sqlite_before_autoincrement",
        lambda: backup_calls.append(Path("dummy")) or Path("dummy"),
    )

    db_module._migrate_pk_to_autoincrement(["chatturn"])
    # 没有 pending 表 → 不会调用备份
    assert backup_calls == []


def test_migrate_pk_runs_against_realistic_model_ddl(tmp_path, monkeypatch):
    """端到端验证：用真实 model DDL 建表 → 入口函数能识别并升级。

    这条 case 等价于复现生产 bug——v1 实现的 regex 在这里会 0 匹配、整轮 no-op，
    导致 `_table_already_autoincrement` 之后仍是 False。新实现用 SQLAlchemy 编译
    出新 DDL，能成功 rebuild。
    """
    engine, _ = _make_legacy_engine(tmp_path)
    monkeypatch.setattr(db_module, "engine", engine)

    db_module._migrate_pk_to_autoincrement(["chatturn"])

    assert db_module._table_already_autoincrement("chatturn")
