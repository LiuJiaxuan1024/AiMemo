"""后台命令池：让 ReAct agent 把长跑服务交给后台执行，并按需轮询状态/输出。

设计要点（与初版相比的关键变化）：
- 子进程 stdout/stderr 重定向到磁盘日志文件，**不再走父进程管道**。
  这样后端进程退出后，子进程依然能继续写日志、继续运行。
- 每次状态变化（spawn / kill / 退出探测）都持久化到 `BackgroundTask` 表。
  后端重启后通过 `adopt_persisted_tasks()` 探活已有 pid，
  把还活着的进程 re-register 到内存池，把已死的标记为 orphaned。
- 输出读取从日志文件按行解析；按行号分页给前端/agent。

安全边界沿用第一版：
- 仍然受 LocalOperatorPolicy / cwd 检查约束；
- 仍然走 evaluate_command_policy 拒绝危险命令；
- 单 conversation 最多 5 个并发后台任务，避免无限派生。
"""

from __future__ import annotations

import ctypes
from dataclasses import dataclass, field
from datetime import datetime, timezone
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time
from typing import Any
import uuid

from sqlmodel import desc, select

from app.local_operator.command import (
    CommandPolicyDecision,
    _has_background_operator,
    _contains_dangerous_command,
    _contains_shell_redirection,
    _looks_interactive,
    _looks_like_download_and_execute,
    _looks_like_file_write_command,
    _normalize_command,
    _safe_subprocess_env,
    _strip_ansi,
    _terminate_process_tree,
)
from app.local_operator.policy import LocalOperatorPolicy
from app.local_operator.schemas import ToolResult
from app.models.background_task import BackgroundTask
from app.models.note import utc_now


_IS_WINDOWS = sys.platform.startswith("win")

MAX_BACKGROUND_TASKS_PER_CONVERSATION = 5
MAX_BACKGROUND_LINES_RETURNED = 200
DEFAULT_BACKGROUND_LINES_RETURNED = 50
MAX_LOG_BYTES_TO_SCAN = 4 * 1024 * 1024  # 单个日志文件单次读取上限 4 MB

_LOG_DIR = Path("data") / "background_logs"


def _ensure_log_dir() -> Path:
    _LOG_DIR.mkdir(parents=True, exist_ok=True)
    return _LOG_DIR


def evaluate_background_command_policy(command: str) -> CommandPolicyDecision:
    """后台启动命令的策略检查。

    与前台 exec 用同一组危险规则，但**允许**长跑服务（uvicorn/flask/npm start 等）。
    """

    lowered = command.lower().strip()
    if _has_background_operator(lowered):
        return CommandPolicyDecision(False, "命令本身已包含 shell 后台符；请直接交给后台工具运行。", "high")
    if _looks_interactive(lowered):
        return CommandPolicyDecision(False, "后台任务不支持交互式命令。", "high")
    if _looks_like_download_and_execute(lowered):
        return CommandPolicyDecision(False, "命令疑似下载后执行远程代码。", "high")
    if _contains_dangerous_command(lowered):
        return CommandPolicyDecision(False, "命令包含删除、格式化、关机、权限提升或破坏性操作。", "high")
    if _contains_shell_redirection(lowered):
        return CommandPolicyDecision(False, "exec 暂不允许 shell 重定向写文件。", "high")
    if _looks_like_file_write_command(lowered):
        return CommandPolicyDecision(False, "exec 不用于文件写入；请使用 write_file 工具。", "high")
    return CommandPolicyDecision(True, "命令未命中高风险规则；允许后台运行。", "medium")


# ---------- PID 探活 ----------

def _probe_pid_alive(pid: int) -> bool:
    """跨平台探活：仅检查 pid 是否还在 OS 中。

    Windows: OpenProcess + GetExitCodeProcess; 若返回 STILL_ACTIVE 则视为存活。
    POSIX:   os.kill(pid, 0)；ProcessLookupError = 已死，PermissionError = 还活着但无权限。
    """

    if pid is None or pid <= 0:
        return False
    if _IS_WINDOWS:
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
        if not handle:
            return False
        try:
            exit_code = ctypes.c_ulong(0)
            ok = kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))
            if not ok:
                return False
            return exit_code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    else:
        try:
            os.kill(int(pid), 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return False


def _kill_pid_tree(pid: int) -> None:
    """对 adopted 任务（没有 Popen 句柄）按 pid 整树杀。"""

    if pid is None or pid <= 0:
        return
    try:
        if _IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(int(pid))],
                capture_output=True,
                timeout=5,
                check=False,
            )
        else:
            try:
                pgid = os.getpgid(int(pid))
                os.killpg(pgid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            time.sleep(0.5)
            try:
                pgid = os.getpgid(int(pid))
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
    except Exception:
        pass


# ---------- 日志文件读取 ----------

def _read_log_file_lines(path: str, *, max_bytes: int = MAX_LOG_BYTES_TO_SCAN) -> list[str]:
    """读取一个日志文件，按行返回。

    文件超过 max_bytes 时只读尾部，丢失早期行（避免内存爆炸）。
    """

    if not path:
        return []
    p = Path(path)
    if not p.exists():
        return []
    try:
        size = p.stat().st_size
        with p.open("rb") as f:
            if size > max_bytes:
                f.seek(size - max_bytes)
                f.readline()  # 丢弃半截行
            data = f.read()
    except OSError:
        return []
    text = data.decode("utf-8", errors="replace")
    text = _strip_ansi(text)
    if not text:
        return []
    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    return lines


def _collect_output_lines(
    stdout_path: str,
    stderr_path: str,
    *,
    since_line: int,
    max_lines: int,
) -> tuple[list[dict[str, Any]], int, bool]:
    """合并 stdout/stderr 行，全局编号，按 since_line 增量切片。

    返回 (lines, last_line, more)。lineno 顺序：先把 stdout 排在前段，
    再续上 stderr 段；这种方式保证 since_line 单调递增即可拿到所有新增内容。
    """

    out_lines = _read_log_file_lines(stdout_path)
    err_lines = _read_log_file_lines(stderr_path)

    enumerated: list[tuple[int, str, str]] = []
    lineno = 1
    for text in out_lines:
        enumerated.append((lineno, "stdout", text))
        lineno += 1
    for text in err_lines:
        enumerated.append((lineno, "stderr", text))
        lineno += 1

    selected: list[dict[str, Any]] = []
    last_line = since_line
    for ln, stream, text in enumerated:
        if ln <= since_line:
            continue
        selected.append({"line": ln, "stream": stream, "text": text})
        last_line = ln
        if len(selected) >= max_lines:
            break
    more = last_line < (lineno - 1)
    return selected, last_line, more


# ---------- 子进程启动（输出落盘） ----------

def _spawn_with_log_files(
    command: str,
    *,
    cwd: Path,
    stdout_file,
    stderr_file,
) -> subprocess.Popen:
    """把 stdout/stderr 直接绑定到磁盘文件 FD，子进程从此和父进程管道脱钩。"""

    kwargs: dict[str, Any] = dict(
        cwd=cwd,
        shell=True,
        stdout=stdout_file,
        stderr=stderr_file,
        stdin=subprocess.DEVNULL,
        env=_safe_subprocess_env(),
        bufsize=0,
    )
    if _IS_WINDOWS:
        creationflags = 0
        # 注意：不要用 DETACHED_PROCESS。它会把子进程的 stdio handle 一起切断，
        # 导致我们传入的日志文件根本写不进去。Windows 上不绑 Job Object 的子进程
        # 在父进程退出后默认就能存活，CREATE_NEW_PROCESS_GROUP + CREATE_NO_WINDOW
        # 已经够用——前者让 Ctrl+Break 可控，后者避免弹黑框。
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        kwargs["creationflags"] = creationflags
        # close_fds=False 确保我们传入的日志文件 HANDLE 真的能被子进程继承。
        kwargs["close_fds"] = False
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(command, **kwargs)


# ---------- 数据模型 ----------

@dataclass
class BackgroundShellTask:
    """内存里的任务句柄。与 DB 中的 BackgroundTask 行一一对应。"""

    task_id: str
    command: str
    cwd: str
    conversation_id: int | None
    started_at: float
    proc: Any = None  # adopted 任务为 None
    pid: int | None = None
    stdout_path: str = ""
    stderr_path: str = ""
    status: str = "running"  # running / exited / failed / killed / orphaned
    exit_code: int | None = None
    finished_at: float | None = None
    _kill_reason: str | None = None
    _stdout_file: Any = None
    _stderr_file: Any = None

    def to_status_dict(self) -> dict[str, Any]:
        return {
            "task_id": self.task_id,
            "command": self.command,
            "cwd": self.cwd,
            "conversation_id": self.conversation_id,
            "pid": self.pid,
            "status": self.status,
            "exit_code": self.exit_code,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "kill_reason": self._kill_reason or "",
            "stdout_path": self.stdout_path,
            "stderr_path": self.stderr_path,
        }


# ---------- DB 持久化辅助 ----------

def _open_session():
    """延迟导入避免循环依赖（database 间接依赖 models）。"""

    from app.core.database import session_scope

    return session_scope()


def _persist_new_task(task: BackgroundShellTask) -> None:
    record = BackgroundTask(
        task_id=task.task_id,
        conversation_id=task.conversation_id,
        command=task.command,
        cwd=task.cwd,
        pid=task.pid,
        status=task.status,
        exit_code=task.exit_code,
        kill_reason=task._kill_reason or "",
        stdout_path=task.stdout_path,
        stderr_path=task.stderr_path,
        started_at=datetime.fromtimestamp(tz=timezone.utc, timestamp=task.started_at),
        created_at=utc_now(),
        updated_at=utc_now(),
    )
    with _open_session() as session:
        session.add(record)
        session.commit()


def _persist_status_update(task: BackgroundShellTask) -> None:
    with _open_session() as session:
        record = session.exec(
            select(BackgroundTask).where(BackgroundTask.task_id == task.task_id)
        ).first()
        if record is None:
            return
        record.status = task.status
        record.exit_code = task.exit_code
        record.kill_reason = task._kill_reason or record.kill_reason
        if task.finished_at is not None:
            record.finished_at = datetime.fromtimestamp(tz=timezone.utc, timestamp=task.finished_at)
        record.updated_at = utc_now()
        session.add(record)
        session.commit()


def _delete_task_record(task_id: str) -> None:
    with _open_session() as session:
        record = session.exec(
            select(BackgroundTask).where(BackgroundTask.task_id == task_id)
        ).first()
        if record is None:
            return
        session.delete(record)
        session.commit()


# ---------- 池 ----------

class BackgroundShellPool:
    """进程级后台命令池。

    单例语义；模块顶层导出 `pool`。后端不再在 shutdown 时杀任务——
    任务的进程是 detached 的，会继续跑；重启时通过 `adopt_persisted_tasks()`
    找回它们的状态。
    """

    def __init__(self) -> None:
        self._tasks: dict[str, BackgroundShellTask] = {}
        self._lock = threading.Lock()

    # ---- 查询 ----

    def list_tasks(self, conversation_id: int | None = None) -> list[BackgroundShellTask]:
        with self._lock:
            tasks = list(self._tasks.values())
        # 刷新一遍状态（pid 探活 + 检测内存任务的退出）
        for t in tasks:
            self._refresh_status(t)
        if conversation_id is None:
            return tasks
        return [t for t in tasks if t.conversation_id == conversation_id]

    def list_persisted(self, conversation_id: int | None = None) -> list[BackgroundTask]:
        """直接查 DB 记录（包括 orphaned/历史任务），UI 列表用。"""

        with _open_session() as session:
            query = select(BackgroundTask).order_by(desc(BackgroundTask.created_at))
            if conversation_id is not None:
                query = query.where(BackgroundTask.conversation_id == conversation_id)
            records = session.exec(query).all()
            # detach
            for r in records:
                session.expunge(r)
            return list(records)

    def get(self, task_id: str) -> BackgroundShellTask | None:
        with self._lock:
            return self._tasks.get(task_id)

    def get_record(self, task_id: str) -> BackgroundTask | None:
        with _open_session() as session:
            record = session.exec(
                select(BackgroundTask).where(BackgroundTask.task_id == task_id)
            ).first()
            if record is not None:
                session.expunge(record)
            return record

    # ---- 启动 ----

    def spawn(
        self,
        *,
        policy: LocalOperatorPolicy,
        command: str,
        cwd: str,
        conversation_id: int | None,
    ) -> ToolResult:
        normalized = _normalize_command(command)
        if not normalized:
            return _error("INVALID_ARGUMENT", "command 不能为空。", blocked=True)
        try:
            resolved_cwd = policy.resolve_authorized_path(cwd or ".")
        except PermissionError as exc:
            return _error(str(exc), "cwd 不在授权 workspace 内。", blocked=True)
        if not resolved_cwd.exists() or not resolved_cwd.is_dir():
            return _error("PATH_NOT_FOUND", "cwd 路径不存在或不是目录。", blocked=True)

        decision = evaluate_background_command_policy(normalized)
        if not decision.allowed:
            return _error(
                "COMMAND_BLOCKED",
                decision.reason,
                blocked=True,
                data={"risk_level": decision.risk_level},
            )

        active = [
            t for t in self.list_tasks(conversation_id=conversation_id)
            if t.status == "running"
        ]
        if len(active) >= MAX_BACKGROUND_TASKS_PER_CONVERSATION:
            return _error(
                "BACKGROUND_TASK_LIMIT",
                (
                    f"本会话已有 {len(active)} 个后台任务在运行，"
                    f"上限 {MAX_BACKGROUND_TASKS_PER_CONVERSATION}；"
                    "请先用 kill_background_task 清理掉不需要的，再启动新任务。"
                ),
                blocked=True,
                data={"active_task_ids": [t.task_id for t in active]},
            )

        task_id = f"bg-{uuid.uuid4().hex[:10]}"
        log_dir = _ensure_log_dir()
        stdout_path = log_dir / f"{task_id}.stdout.log"
        stderr_path = log_dir / f"{task_id}.stderr.log"

        try:
            stdout_file = open(stdout_path, "wb")
            stderr_file = open(stderr_path, "wb")
        except OSError as exc:
            return _error("EXEC_FAILED", f"打开日志文件失败：{exc}")

        try:
            proc = _spawn_with_log_files(
                normalized,
                cwd=resolved_cwd,
                stdout_file=stdout_file,
                stderr_file=stderr_file,
            )
        except OSError as exc:
            stdout_file.close()
            stderr_file.close()
            return _error("EXEC_FAILED", f"后台命令启动失败：{exc}")

        task = BackgroundShellTask(
            task_id=task_id,
            command=normalized,
            cwd=resolved_cwd.as_posix(),
            conversation_id=conversation_id,
            started_at=time.time(),
            proc=proc,
            pid=proc.pid,
            stdout_path=str(stdout_path),
            stderr_path=str(stderr_path),
            _stdout_file=stdout_file,
            _stderr_file=stderr_file,
        )
        with self._lock:
            self._tasks[task_id] = task

        try:
            _persist_new_task(task)
        except Exception:
            # 测试或 DB 尚未初始化时跳过持久化；内存池仍可用。
            pass
        self._start_watcher_thread(task)

        # 给进程 ~300ms 让它要么立刻崩、要么开始 listen，便于 agent 拿到首条反馈。
        time.sleep(0.3)
        self._refresh_status(task)

        return ToolResult(
            ok=True,
            tool_name="exec_command_background",
            data={
                **task.to_status_dict(),
                "risk_level": decision.risk_level,
                "hint": "用 read_background_output 轮询输出；用 kill_background_task 停止。",
            },
        )

    # ---- 输出读取 ----

    def read_output(
        self,
        task_id: str,
        *,
        since_line: int = 0,
        max_lines: int = DEFAULT_BACKGROUND_LINES_RETURNED,
    ) -> ToolResult:
        task = self.get(task_id)
        if task is None:
            # 也尝试从 DB 读（orphaned 任务仍有日志可看）
            record = self.get_record(task_id)
            if record is None:
                return _error("BACKGROUND_TASK_NOT_FOUND", f"找不到后台任务：{task_id}", blocked=True)
            stdout_path = record.stdout_path
            stderr_path = record.stderr_path
            status_dict = _record_to_status_dict(record)
        else:
            self._refresh_status(task)
            stdout_path = task.stdout_path
            stderr_path = task.stderr_path
            status_dict = task.to_status_dict()

        max_lines = max(1, min(int(max_lines or DEFAULT_BACKGROUND_LINES_RETURNED), MAX_BACKGROUND_LINES_RETURNED))
        lines, last_line, more = _collect_output_lines(
            stdout_path,
            stderr_path,
            since_line=max(0, int(since_line or 0)),
            max_lines=max_lines,
        )
        return ToolResult(
            ok=True,
            tool_name="read_background_output",
            data={
                **status_dict,
                "lines": lines,
                "last_line": last_line,
                "dropped_lines": 0,
                "more": more,
            },
        )

    # ---- 终止 ----

    def kill(self, task_id: str, *, reason: str = "killed by tool call") -> ToolResult:
        task = self.get(task_id)
        if task is None:
            # 试试 DB 里有没有，如果有且 running，按 pid 杀
            record = self.get_record(task_id)
            if record is None:
                return _error("BACKGROUND_TASK_NOT_FOUND", f"找不到后台任务：{task_id}", blocked=True)
            if record.status == "running" and record.pid:
                _kill_pid_tree(record.pid)
                record.status = "killed"
                record.kill_reason = reason
                record.finished_at = utc_now()
                with _open_session() as session:
                    db_record = session.exec(
                        select(BackgroundTask).where(BackgroundTask.task_id == task_id)
                    ).first()
                    if db_record is not None:
                        db_record.status = "killed"
                        db_record.kill_reason = reason
                        db_record.finished_at = utc_now()
                        db_record.updated_at = utc_now()
                        session.add(db_record)
                        session.commit()
            return ToolResult(
                ok=True,
                tool_name="kill_background_task",
                data={**_record_to_status_dict(record), "message": "后台任务已停止。"},
            )

        if task.status == "running":
            task._kill_reason = reason
            if task.proc is not None:
                _terminate_process_tree(task.proc)
                try:
                    task.proc.wait(timeout=2.0)
                except Exception:
                    pass
            else:
                # adopted task：按 pid 杀
                if task.pid:
                    _kill_pid_tree(task.pid)
                task.status = "killed"
                task.finished_at = time.time()
                try:
                    _persist_status_update(task)
                except Exception:
                    pass
            # watcher thread 会处理 status / file close（fresh task）
            self._refresh_status(task, default_status="killed")

        return ToolResult(
            ok=True,
            tool_name="kill_background_task",
            data={**task.to_status_dict(), "message": "后台任务已停止。"},
        )

    def prune(self, task_id: str) -> ToolResult:
        """从池和 DB 里移除一个已结束的任务（清理 UI 列表用）。"""

        with self._lock:
            task = self._tasks.get(task_id)
            if task is not None and task.status == "running":
                return _error("BACKGROUND_TASK_RUNNING", "任务仍在运行，请先 kill 再 prune。", blocked=True)
            self._tasks.pop(task_id, None)
        try:
            _delete_task_record(task_id)
        except Exception:
            pass
        return ToolResult(
            ok=True,
            tool_name="prune_background_task",
            data={"task_id": task_id, "message": "已从列表移除。"},
        )

    # ---- 启动时 adopt ----

    def adopt_persisted_tasks(self) -> dict[str, int]:
        """后端启动时调用。

        - 找出所有 DB 里 status==running 的记录；
        - 探活 pid：还在 → 注册到内存池作为 adopted 任务；
          不在 → 标记为 orphaned，写回 DB。

        返回 {"adopted": n, "orphaned": n}，用于日志/诊断。
        """

        adopted = 0
        orphaned = 0
        with _open_session() as session:
            records = session.exec(
                select(BackgroundTask).where(BackgroundTask.status == "running")
            ).all()
            for record in records:
                alive = _probe_pid_alive(record.pid) if record.pid else False
                if alive:
                    task = BackgroundShellTask(
                        task_id=record.task_id,
                        command=record.command,
                        cwd=record.cwd,
                        conversation_id=record.conversation_id,
                        started_at=record.started_at.timestamp() if record.started_at else time.time(),
                        proc=None,
                        pid=record.pid,
                        stdout_path=record.stdout_path,
                        stderr_path=record.stderr_path,
                        status="running",
                    )
                    with self._lock:
                        self._tasks[record.task_id] = task
                    adopted += 1
                else:
                    record.status = "orphaned"
                    record.finished_at = utc_now()
                    record.kill_reason = record.kill_reason or "backend restart: process not found"
                    record.updated_at = utc_now()
                    session.add(record)
                    orphaned += 1
            session.commit()
        return {"adopted": adopted, "orphaned": orphaned}

    def cleanup_finished_tasks(self) -> dict[str, int]:
        """删掉所有已经终止的后台任务记录（exited / failed / killed / orphaned / unknown）。

        启动钩子调用：先 adopt 把死掉的标记成 orphaned，再调用本方法清理，
        让 DB 和 UI 列表只保留真正还在跑的任务；同时删除对应的 stdout / stderr
        日志文件，避免长期累积。

        返回 `{"removed": N, "logs_deleted": M}`，方便日志/监控观察。
        """

        terminal_statuses = {"exited", "failed", "killed", "orphaned", "unknown"}
        removed = 0
        logs_deleted = 0
        cleaned_task_ids: list[str] = []

        with _open_session() as session:
            records = session.exec(select(BackgroundTask)).all()
            for record in records:
                if record.status not in terminal_statuses:
                    continue
                for log_path in (record.stdout_path, record.stderr_path):
                    if not log_path:
                        continue
                    try:
                        Path(log_path).unlink(missing_ok=True)
                        logs_deleted += 1
                    except OSError:
                        # 日志文件被占用 / 已被外部删除：忽略，主目标是清 DB。
                        pass
                cleaned_task_ids.append(record.task_id)
                session.delete(record)
                removed += 1
            session.commit()

        # 同步移除内存池中已终止的任务（adopted 任务可能还残留在 _tasks 中）
        with self._lock:
            for task_id in list(self._tasks.keys()):
                task = self._tasks[task_id]
                if task.status in terminal_statuses or task_id in cleaned_task_ids:
                    self._tasks.pop(task_id, None)

        return {"removed": removed, "logs_deleted": logs_deleted}

    def shutdown_all(self) -> None:
        """旧接口；现在只关日志 FD，不杀子进程。

        子进程是 detached 的，会继续运行；DB 里的 running 状态不变，
        下次启动通过 adopt_persisted_tasks() 重新发现。
        """

        with self._lock:
            tasks = list(self._tasks.values())
        for task in tasks:
            for f in (task._stdout_file, task._stderr_file):
                if f is None:
                    continue
                try:
                    f.flush()
                    f.close()
                except Exception:
                    pass

    # ---- 内部 ----

    def _start_watcher_thread(self, task: BackgroundShellTask) -> None:
        """每个 fresh 任务一个守望线程：阻塞 wait，进程退出后更新状态 + 持久化 + 关 FD。"""

        if task.proc is None:
            return

        def watcher() -> None:
            try:
                rc = task.proc.wait()
            except Exception:
                rc = -1
            task.exit_code = int(rc) if rc is not None else None
            task.finished_at = time.time()
            if task.status == "running":
                if task._kill_reason:
                    task.status = "killed"
                else:
                    task.status = "exited" if rc == 0 else "failed"
            for f in (task._stdout_file, task._stderr_file):
                if f is None:
                    continue
                try:
                    f.flush()
                    f.close()
                except Exception:
                    pass
            try:
                _persist_status_update(task)
            except Exception:
                pass

        t = threading.Thread(target=watcher, daemon=True, name=f"bg-{task.task_id}-watch")
        t.start()

    def _refresh_status(self, task: BackgroundShellTask, *, default_status: str = "exited") -> None:
        """把进程当前状态同步到 task；adopted 任务用 pid 探活。"""

        if task.status != "running":
            return
        if task.proc is not None:
            rc = task.proc.poll()
            if rc is None:
                return
            task.exit_code = int(rc)
            task.finished_at = time.time()
            if task._kill_reason or default_status == "killed":
                task.status = "killed"
            else:
                task.status = default_status if rc == 0 else "failed"
            try:
                _persist_status_update(task)
            except Exception:
                pass
        else:
            # adopted: 没有 Popen，按 pid 探活
            if not _probe_pid_alive(task.pid or 0):
                task.status = "orphaned"
                task.finished_at = time.time()
                try:
                    _persist_status_update(task)
                except Exception:
                    pass


pool = BackgroundShellPool()


# ---------- 工具函数 ----------

def _record_to_status_dict(record: BackgroundTask) -> dict[str, Any]:
    return {
        "task_id": record.task_id,
        "command": record.command,
        "cwd": record.cwd,
        "conversation_id": record.conversation_id,
        "pid": record.pid,
        "status": record.status,
        "exit_code": record.exit_code,
        "started_at": record.started_at.timestamp() if record.started_at else None,
        "finished_at": record.finished_at.timestamp() if record.finished_at else None,
        "kill_reason": record.kill_reason or "",
        "stdout_path": record.stdout_path,
        "stderr_path": record.stderr_path,
    }


def _error(error_code: str, message: str, *, blocked: bool = False, data: dict[str, Any] | None = None) -> ToolResult:
    return ToolResult(
        ok=False,
        tool_name="background_command",
        data=data or {},
        error_code=error_code,
        message=message,
        blocked=blocked,
    )
