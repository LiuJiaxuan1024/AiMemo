"""B/D/E 三块新增能力的单元测试。

覆盖：
- B: evaluate_command_policy 必须拦截 flask/uvicorn/npm start 等长跑服务命令；
- D: LocalCommandExecutor 在超时时能整树清理子进程；
- E: BackgroundShellPool spawn/read/kill 链路 + 危险命令拦截 + 单会话上限。
"""

from __future__ import annotations

from pathlib import Path
import sys
import time

import pytest

from app.local_operator.background_command import (
    BackgroundShellPool,
    MAX_BACKGROUND_TASKS_PER_CONVERSATION,
    evaluate_background_command_policy,
)
from app.local_operator.command import LocalCommandExecutor, evaluate_command_policy
from app.local_operator.policy import LocalOperatorPolicy


_IS_WINDOWS = sys.platform.startswith("win")


# ---------- B: 长跑服务策略 ----------

@pytest.mark.parametrize(
    "command",
    [
        "flask run",
        "uvicorn app:app --reload",
        "python -m uvicorn main:app",
        "python manage.py runserver 0.0.0.0:8000",
        "npm start",
        "npm run dev",
        "pnpm run serve",
        "yarn start",
        "next dev",
        "python app.py",
        "python -m http.server 8000",
        "node server.js",
        "node app/index.js",
        "gunicorn wsgi:app",
        "docker compose up",
        "go run ./cmd/server",
        "rails s",
        "php -S 0.0.0.0:8080",
    ],
)
def test_policy_blocks_long_running_server_commands(command: str) -> None:
    """这些命令前台跑就会阻塞 agent 循环，必须强制走后台。"""

    decision = evaluate_command_policy(command)
    assert decision.allowed is False
    assert "后台" in decision.reason or "exec_command_background" in decision.reason


@pytest.mark.parametrize(
    "command",
    [
        "python --version",
        "git status",
        "ls",
        "pytest --version",
        # 含 -d 的 docker run 不算长跑前台命令
        "docker run -d --rm nginx",
        "docker compose up -d",
    ],
)
def test_policy_allows_short_commands(command: str) -> None:
    decision = evaluate_command_policy(command)
    assert decision.allowed is True


# ---------- D: 超时后进程整树清理 ----------

def test_exec_command_timeout_kills_process_tree(tmp_path: Path) -> None:
    """超时必须返回 COMMAND_TIMEOUT，且子进程不能残留。"""

    workspace = tmp_path / "ws"
    workspace.mkdir()
    executor = LocalCommandExecutor(LocalOperatorPolicy.from_roots([str(workspace)]))

    # 1.5s 的睡眠，800ms 超时——子进程必然被杀。
    cmd = "python -c \"import time; time.sleep(1.5)\""
    started = time.perf_counter()
    result = executor.exec_command(command=cmd, cwd=".", timeout_ms=800)
    elapsed = time.perf_counter() - started

    assert result.ok is False
    assert result.data.get("timed_out") is True
    assert result.error_code == "COMMAND_TIMEOUT"
    # 进程树清理本身不应该让总耗时拖很长（留 4s buffer 给 Windows taskkill）。
    assert elapsed < 5.0


# ---------- E: 后台任务池策略 ----------

@pytest.mark.parametrize(
    "command",
    [
        "rm -rf /",
        "git reset --hard",
        "shutdown -h now",
        "sudo apt install vim",
    ],
)
def test_background_policy_blocks_dangerous_commands(command: str) -> None:
    decision = evaluate_background_command_policy(command)
    assert decision.allowed is False


def test_background_policy_allows_long_running_servers() -> None:
    """前台 exec 拦截的长跑服务，恰好是后台模式应该放行的。"""

    for cmd in ["flask run", "uvicorn app:app", "npm start", "python app.py"]:
        decision = evaluate_background_command_policy(cmd)
        assert decision.allowed is True, f"应允许后台运行：{cmd}"


# ---------- E: 后台任务 spawn/read/kill ----------

def _make_pool_and_policy(tmp_path: Path) -> tuple[BackgroundShellPool, LocalOperatorPolicy, str]:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    return (
        BackgroundShellPool(),
        LocalOperatorPolicy.from_roots([str(workspace)]),
        str(workspace),
    )


def test_background_spawn_returns_task_id(tmp_path: Path) -> None:
    pool, policy, ws = _make_pool_and_policy(tmp_path)

    cmd = "python -c \"import time, sys; sys.stdout.write('hello\\n'); sys.stdout.flush(); time.sleep(1.5)\""
    result = pool.spawn(policy=policy, command=cmd, cwd=ws, conversation_id=42)

    assert result.ok is True
    task_id = result.data["task_id"]
    assert task_id.startswith("bg-")
    try:
        # spawn 自带 ~300ms 等待，应该已经看到首行输出
        time.sleep(0.4)
        read_result = pool.read_output(task_id, since_line=0, max_lines=10)
        assert read_result.ok is True
        texts = [line["text"] for line in read_result.data["lines"]]
        assert any("hello" in t for t in texts)
    finally:
        pool.kill(task_id)


def test_background_kill_terminates_running_task(tmp_path: Path) -> None:
    pool, policy, ws = _make_pool_and_policy(tmp_path)

    cmd = "python -c \"import time; [time.sleep(0.5) for _ in range(20)]\""
    spawn = pool.spawn(policy=policy, command=cmd, cwd=ws, conversation_id=1)
    task_id = spawn.data["task_id"]

    kill_result = pool.kill(task_id)
    assert kill_result.ok is True
    assert kill_result.data["status"] in {"killed", "exited", "failed"}

    time.sleep(0.3)
    proc = pool.get(task_id).proc
    assert proc.poll() is not None, "kill 之后子进程必须已退出"


def test_background_pool_enforces_per_conversation_limit(tmp_path: Path) -> None:
    pool, policy, ws = _make_pool_and_policy(tmp_path)

    long_cmd = "python -c \"import time; time.sleep(5)\""
    spawned_ids = []
    try:
        for _ in range(MAX_BACKGROUND_TASKS_PER_CONVERSATION):
            r = pool.spawn(policy=policy, command=long_cmd, cwd=ws, conversation_id=7)
            assert r.ok is True
            spawned_ids.append(r.data["task_id"])

        overflow = pool.spawn(policy=policy, command=long_cmd, cwd=ws, conversation_id=7)
        assert overflow.ok is False
        assert overflow.error_code == "BACKGROUND_TASK_LIMIT"
    finally:
        for tid in spawned_ids:
            pool.kill(tid)


def test_background_read_returns_status_for_exited_task(tmp_path: Path) -> None:
    pool, policy, ws = _make_pool_and_policy(tmp_path)

    spawn = pool.spawn(
        policy=policy,
        command="python -c \"print('once')\"",
        cwd=ws,
        conversation_id=None,
    )
    task_id = spawn.data["task_id"]
    # 等待自然退出
    for _ in range(20):
        time.sleep(0.1)
        snap = pool.read_output(task_id, since_line=0, max_lines=10)
        if snap.data["status"] != "running":
            break
    snap = pool.read_output(task_id, since_line=0, max_lines=10)
    assert snap.ok is True
    assert snap.data["status"] in {"exited", "failed"}
    assert snap.data["exit_code"] == 0
    assert any("once" in line["text"] for line in snap.data["lines"])


def test_background_shutdown_does_not_kill_running_tasks(tmp_path: Path) -> None:
    """新行为：shutdown_all 不再杀子进程。

    后端关闭时如果顺手杀掉用户启动的应用（例如 uvicorn 服务），是不合理的。
    detached 子进程应该继续运行；下次启动时通过 adopt_persisted_tasks 重新发现。
    """

    pool, policy, ws = _make_pool_and_policy(tmp_path)

    spawn = pool.spawn(
        policy=policy,
        command="python -c \"import time; time.sleep(10)\"",
        cwd=ws,
        conversation_id=999,
    )
    task_id = spawn.data["task_id"]

    pool.shutdown_all()
    time.sleep(0.3)
    proc = pool.get(task_id).proc
    assert proc.poll() is None, "shutdown_all 不应该 kill 子进程"

    # 清理：手动 kill 别给系统留 zombie
    pool.kill(task_id)
    time.sleep(0.3)
    assert proc.poll() is not None


# ---------- 参数规整：避免把模型给的 command 丢成 {} ----------

def test_normalize_tool_arguments_preserves_background_command_args() -> None:
    """新的后台工具必须在 _normalize_tool_arguments 里有显式分支，否则参数会被冲掉成 {}。"""

    from app.agent.graphs.local_operator.nodes import _normalize_tool_arguments

    bg = _normalize_tool_arguments("exec_command_background", {"command": "uvicorn app:app", "cwd": "E:/demo"})
    assert bg["command"] == "uvicorn app:app"
    assert bg["cwd"] == "E:/demo"

    read = _normalize_tool_arguments("read_background_output", {"task_id": "bg-abc", "since_line": 12, "max_lines": 30})
    assert read == {"task_id": "bg-abc", "since_line": 12, "max_lines": 30}

    kill = _normalize_tool_arguments("kill_background_task", {"task_id": "bg-abc"})
    assert kill == {"task_id": "bg-abc"}
