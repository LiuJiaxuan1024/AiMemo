"""测试后台任务持久化层：

- list_background_tasks 工具的参数规整 & list 行为
- BackgroundShellPool 在 DB 可用时写入 BackgroundTask 行
- adopt_persisted_tasks 找回还活着的进程、把已死的标记为 orphaned
- _probe_pid_alive 跨平台至少能识别自己的 pid 和不存在的 pid
"""

from __future__ import annotations

from contextlib import contextmanager
import os
from pathlib import Path
import sys
import time

import pytest
from sqlmodel import Session, SQLModel, create_engine, select

import app.core.database as core_db
import app.local_operator.background_command as bg
from app.local_operator.background_command import (
    BackgroundShellPool,
    _probe_pid_alive,
)
from app.local_operator.policy import LocalOperatorPolicy
from app.models import BackgroundTask  # noqa: F401  确保 metadata 注册


def _make_policy(tmp_path: Path) -> LocalOperatorPolicy:
    workspace = tmp_path / "ws"
    workspace.mkdir(parents=True, exist_ok=True)
    return LocalOperatorPolicy(workspace_roots=(workspace.resolve(),))


@pytest.fixture
def isolated_db(tmp_path: Path, monkeypatch):
    """把 app.core.database 的全局 engine 重定向到 tmp DB，
    并切到 tmp_path 作为 cwd（log 文件相对路径 ./data/background_logs/）。"""

    db_path = tmp_path / "test_bg.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
    )
    SQLModel.metadata.create_all(engine)

    @contextmanager
    def fake_session_scope():
        with Session(engine) as session:
            yield session

    monkeypatch.setattr(core_db, "engine", engine)
    monkeypatch.setattr(core_db, "session_scope", fake_session_scope)

    original_cwd = Path.cwd()
    os.chdir(tmp_path)
    try:
        yield engine
    finally:
        os.chdir(original_cwd)


def test_probe_pid_alive_self() -> None:
    """当前 Python 进程 pid 应被识别为存活。"""

    assert _probe_pid_alive(os.getpid()) is True


def test_probe_pid_alive_nonexistent() -> None:
    """一个明显不存在的大 pid 应被识别为已死。"""

    # 99,000,000 大概率不会被任何系统分配
    assert _probe_pid_alive(99_000_000) is False


def test_probe_pid_alive_zero_or_negative() -> None:
    assert _probe_pid_alive(0) is False
    assert _probe_pid_alive(-1) is False


def test_spawn_persists_background_task_row(tmp_path: Path, isolated_db) -> None:
    pool = BackgroundShellPool()
    policy = _make_policy(tmp_path)

    cmd = f'{sys.executable} -c "import time; time.sleep(1.0)"'
    result = pool.spawn(policy=policy, command=cmd, cwd=str(policy.workspace_roots[0]), conversation_id=123)
    assert result.ok is True
    task_id = result.data["task_id"]

    try:
        with Session(isolated_db) as session:
            records = session.exec(select(BackgroundTask)).all()
            assert len(records) == 1
            r = records[0]
            assert r.task_id == task_id
            assert r.conversation_id == 123
            assert r.status == "running"
            assert r.pid is not None
            assert r.stdout_path.endswith(".stdout.log")
            assert r.stderr_path.endswith(".stderr.log")
    finally:
        pool.kill(task_id)


def test_watcher_updates_status_on_exit(tmp_path: Path, isolated_db) -> None:
    pool = BackgroundShellPool()
    policy = _make_policy(tmp_path)

    # 立刻退出，exit 0
    cmd = f'{sys.executable} -c "pass"'
    result = pool.spawn(policy=policy, command=cmd, cwd=str(policy.workspace_roots[0]), conversation_id=1)
    task_id = result.data["task_id"]

    # 等 watcher 把状态写回
    deadline = time.time() + 5.0
    while time.time() < deadline:
        with Session(isolated_db) as session:
            r = session.exec(select(BackgroundTask).where(BackgroundTask.task_id == task_id)).first()
            if r is not None and r.status != "running":
                assert r.status == "exited"
                assert r.exit_code == 0
                assert r.finished_at is not None
                return
        time.sleep(0.1)
    pytest.fail("watcher 没有在 5 秒内把任务状态更新为 exited")


def test_adopt_persisted_tasks_marks_dead_pid_orphaned(tmp_path: Path, isolated_db) -> None:
    """重启场景：DB 里有 status=running 但 pid 已经不在 → 应该被标为 orphaned。"""

    with Session(isolated_db) as session:
        from datetime import datetime
        record = BackgroundTask(
            task_id="bg-fakeghost1",
            conversation_id=42,
            command="echo zombie",
            cwd=str(tmp_path),
            pid=99_000_000,  # 不存在
            status="running",
            stdout_path=str(tmp_path / "bg-fakeghost1.stdout.log"),
            stderr_path=str(tmp_path / "bg-fakeghost1.stderr.log"),
            started_at=datetime.utcnow(),
        )
        session.add(record)
        session.commit()

    pool = BackgroundShellPool()
    stats = pool.adopt_persisted_tasks()
    assert stats["orphaned"] == 1
    assert stats["adopted"] == 0

    with Session(isolated_db) as session:
        r = session.exec(select(BackgroundTask).where(BackgroundTask.task_id == "bg-fakeghost1")).first()
        assert r.status == "orphaned"
        assert r.finished_at is not None
        assert "process not found" in (r.kill_reason or "")


def test_adopt_persisted_tasks_keeps_alive_pid(tmp_path: Path, isolated_db) -> None:
    """重启场景：pid 还活着 → adopted，注册到内存池（无 Popen，按 pid 操作）。"""

    pool = BackgroundShellPool()
    policy = _make_policy(tmp_path)
    cmd = f'{sys.executable} -c "import time; time.sleep(5.0)"'
    result = pool.spawn(policy=policy, command=cmd, cwd=str(policy.workspace_roots[0]), conversation_id=7)
    task_id = result.data["task_id"]

    try:
        # 模拟"重启"：丢弃旧池，用新池 adopt
        fresh_pool = BackgroundShellPool()
        stats = fresh_pool.adopt_persisted_tasks()
        assert stats["adopted"] == 1
        assert stats["orphaned"] == 0
        adopted_task = fresh_pool.get(task_id)
        assert adopted_task is not None
        assert adopted_task.proc is None  # adopted 没有 Popen 句柄
        assert adopted_task.pid is not None
        assert adopted_task.status == "running"

        # kill adopted 任务：走 pid kill 分支
        kill_result = fresh_pool.kill(task_id)
        assert kill_result.ok is True
    finally:
        pool.kill(task_id)


def test_normalize_tool_arguments_for_list_background_tasks() -> None:
    """list_background_tasks 必须在 normalize 里有显式分支，否则参数会丢成 {}."""

    from app.agent.graphs.local_operator.nodes import _normalize_tool_arguments

    default = _normalize_tool_arguments("list_background_tasks", {})
    assert default == {"include_finished": True}

    explicit_true = _normalize_tool_arguments("list_background_tasks", {"include_finished": True})
    assert explicit_true == {"include_finished": True}

    explicit_false = _normalize_tool_arguments("list_background_tasks", {"include_finished": False})
    assert explicit_false == {"include_finished": False}

    string_false = _normalize_tool_arguments("list_background_tasks", {"include_finished": "false"})
    assert string_false == {"include_finished": False}


def test_cleanup_finished_tasks_removes_terminal_records_and_logs(
    tmp_path: Path,
    isolated_db,
) -> None:
    """启动钩子语义：cleanup_finished_tasks 删掉所有非 running 的记录 + 日志文件，
    仍在跑的任务保持不动。"""

    from datetime import datetime

    stdout_files: dict[str, Path] = {}
    stderr_files: dict[str, Path] = {}
    seeded = [
        ("bg-exited-1", "exited"),
        ("bg-failed-2", "failed"),
        ("bg-killed-3", "killed"),
        ("bg-orphan-4", "orphaned"),
        ("bg-running-5", "running"),
    ]
    with Session(isolated_db) as session:
        for task_id, status in seeded:
            stdout_path = tmp_path / f"{task_id}.stdout.log"
            stderr_path = tmp_path / f"{task_id}.stderr.log"
            stdout_path.write_text("out", encoding="utf-8")
            stderr_path.write_text("err", encoding="utf-8")
            stdout_files[task_id] = stdout_path
            stderr_files[task_id] = stderr_path
            session.add(
                BackgroundTask(
                    task_id=task_id,
                    conversation_id=1,
                    command="echo hi",
                    cwd=str(tmp_path),
                    pid=12345,
                    status=status,
                    stdout_path=str(stdout_path),
                    stderr_path=str(stderr_path),
                    started_at=datetime.utcnow(),
                )
            )
        session.commit()

    pool = BackgroundShellPool()
    stats = pool.cleanup_finished_tasks()

    # 4 个终止态被清掉；每个 2 个日志文件 → 8 个文件
    assert stats == {"removed": 4, "logs_deleted": 8}

    with Session(isolated_db) as session:
        remaining = session.exec(select(BackgroundTask)).all()
        assert [r.task_id for r in remaining] == ["bg-running-5"]
        assert remaining[0].status == "running"

    for task_id, _ in seeded[:4]:
        assert not stdout_files[task_id].exists()
        assert not stderr_files[task_id].exists()
    assert stdout_files["bg-running-5"].exists()
    assert stderr_files["bg-running-5"].exists()


def test_cleanup_finished_tasks_is_safe_on_empty_db(isolated_db) -> None:
    pool = BackgroundShellPool()
    stats = pool.cleanup_finished_tasks()
    assert stats == {"removed": 0, "logs_deleted": 0}
