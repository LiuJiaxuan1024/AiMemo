from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import time
from typing import Any

from app.core.config import settings
from app.local_operator.policy import LocalOperatorPolicy
from app.local_operator.schemas import ToolResult


_IS_WINDOWS = sys.platform.startswith("win")


DEFAULT_EXEC_TIMEOUT_MS = settings.local_operator_exec_default_timeout_ms
MAX_EXEC_TIMEOUT_MS = settings.local_operator_exec_max_timeout_ms
DEFAULT_MAX_OUTPUT_BYTES = settings.local_operator_exec_default_max_output_bytes
MAX_OUTPUT_BYTES = settings.local_operator_exec_max_output_bytes


@dataclass(frozen=True)
class CommandPolicyDecision:
    """exec 命令策略判断结果。

    allowed 表示是否允许执行；risk_level 会写入审计表，也会回传给 graph 调试面板。
    当前第一版只自动允许低风险/中风险的非交互命令；高风险命令直接拦截。
    """

    allowed: bool
    reason: str
    risk_level: str = "medium"


class LocalCommandExecutor:
    """受控终端命令执行器。

    这个类只负责确定性命令执行，不依赖 LangChain 或数据库。安全边界包括：
      - cwd 必须位于授权 workspace roots 内。
      - 禁止明显破坏性、交互式、下载执行、权限提升和系统关机类命令。
      - 强制超时和输出截断。
      - 不接受后台任务；后续如需长任务，应走 jobs + approval/interrupt。
    """

    def __init__(self, policy: LocalOperatorPolicy):
        self.policy = policy

    def exec_command(
        self,
        *,
        command: str,
        cwd: str = ".",
        timeout_ms: int = DEFAULT_EXEC_TIMEOUT_MS,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> ToolResult:
        """执行一条短时终端命令，并返回 stdout/stderr/exit_code。"""

        normalized_command = _normalize_command(command)
        if not normalized_command:
            return _error("INVALID_ARGUMENT", "command 不能为空。", blocked=True)

        try:
            resolved_cwd = self.policy.resolve_authorized_path(cwd or ".")
        except PermissionError as exc:
            return _error(str(exc), _policy_error_message(str(exc)), blocked=True)
        if not resolved_cwd.exists():
            return _error("PATH_NOT_FOUND", "cwd 路径不存在。")
        if not resolved_cwd.is_dir():
            return _error("PATH_IS_FILE", "cwd 必须是目录。")

        decision = evaluate_command_policy(normalized_command)
        if not decision.allowed:
            return _error("COMMAND_BLOCKED", decision.reason, blocked=True, data={"risk_level": decision.risk_level})

        timeout_ms = min(max(int(timeout_ms or DEFAULT_EXEC_TIMEOUT_MS), 1_000), MAX_EXEC_TIMEOUT_MS)
        max_output_bytes = min(max(int(max_output_bytes or DEFAULT_MAX_OUTPUT_BYTES), 1_024), MAX_OUTPUT_BYTES)
        started_at = time.perf_counter()
        timed_out = False
        pipe_broken = False
        exit_code = -1
        stdout = ""
        stderr = ""
        try:
            proc = _spawn_subprocess(normalized_command, cwd=resolved_cwd)
        except OSError as exc:
            return _error("EXEC_FAILED", f"命令启动失败：{exc}")

        try:
            try:
                stdout_raw, stderr_raw = proc.communicate(timeout=timeout_ms / 1000)
                exit_code = int(proc.returncode)
                stdout = stdout_raw or ""
                stderr = stderr_raw or ""
            except subprocess.TimeoutExpired:
                timed_out = True
                _terminate_process_tree(proc)
                try:
                    stdout_raw, stderr_raw = proc.communicate(timeout=2.0)
                except subprocess.TimeoutExpired:
                    stdout_raw, stderr_raw = "", ""
                exit_code = -1
                stdout = stdout_raw or ""
                stderr = stderr_raw or f"命令执行超过 {timeout_ms}ms，已终止。"
            except OSError as exc:
                # Windows 上当子进程被外部强杀、stdout/stderr 管道断开时，
                # communicate 可能抛出 WinError 233。这个场景不应把整个 agent loop
                # 炸掉，而应转成可观测的工具失败，让上层根据 stderr / exit_code 重规划。
                pipe_broken = True
                exit_code = proc.returncode if proc.returncode is not None else -1
                stdout = ""
                stderr = f"命令执行时管道断开：{exc}"
        finally:
            # 防御性兜底：若上面任意分支异常，确保子进程及其孙子进程不残留。
            if proc.poll() is None:
                _terminate_process_tree(proc)

        stdout = _strip_ansi(stdout)
        stderr = _strip_ansi(stderr)
        stdout, stderr, truncated = _truncate_outputs(stdout, stderr, max_output_bytes=max_output_bytes)
        # 子进程成功启动不等于命令成功。agent 的重规划依赖 ok=false 来识别失败，
        # 因此非 0 退出码必须作为工具失败回传，同时保留 stdout/stderr 供后续判断。
        failed_with_exit_code = (not timed_out) and exit_code != 0
        ok = (not timed_out) and (not failed_with_exit_code) and (not pipe_broken)
        error_code = (
            "COMMAND_TIMEOUT"
            if timed_out
            else ("COMMAND_PIPE_BROKEN" if pipe_broken else ("COMMAND_EXITED_NON_ZERO" if failed_with_exit_code else ""))
        )
        message = (
            "命令超时，已终止。"
            if timed_out
            else (
                "命令执行时管道断开。"
                if pipe_broken
                else ("命令以非 0 状态退出。" if failed_with_exit_code else "")
            )
        )
        return ToolResult(
            ok=ok,
            tool_name="exec_command",
            data={
                "command": normalized_command,
                "cwd": resolved_cwd.as_posix(),
                "relative_cwd": self.policy.relative_path(resolved_cwd),
                "exit_code": exit_code,
                "stdout": stdout,
                "stderr": stderr,
                "duration_ms": int((time.perf_counter() - started_at) * 1000),
                "timed_out": timed_out,
                "pipe_broken": pipe_broken,
                "truncated": truncated,
                "risk_level": decision.risk_level,
            },
            error_code=error_code,
            message=message,
            blocked=False,
        )


def evaluate_command_policy(command: str) -> CommandPolicyDecision:
    """判断命令是否允许执行。

    这里借鉴 通用 coding agent 的 Bash/PowerShell 思路，但第一版不尝试完整解析 shell AST。
    解析不完整时要保守：命中危险符号或危险命令就拦截，普通开发命令先允许短时执行。
    """

    lowered = command.lower().strip()
    if _has_background_operator(lowered):
        return CommandPolicyDecision(False, "暂不支持后台命令或后台运算符。", "high")
    if _looks_interactive(lowered):
        return CommandPolicyDecision(False, "暂不支持交互式命令；远程 SSH/SCP 操作请使用远程操作工具。", "high")
    if _looks_like_download_and_execute(lowered):
        return CommandPolicyDecision(False, "命令疑似下载后执行远程代码。", "high")
    if _contains_dangerous_command(lowered):
        return CommandPolicyDecision(False, "命令包含删除、格式化、关机、权限提升或破坏性操作。", "high")
    if _contains_shell_redirection(lowered):
        return CommandPolicyDecision(False, "exec 暂不允许 shell 重定向写文件；写文件请使用 write_file。", "high")
    if _looks_like_file_write_command(lowered):
        return CommandPolicyDecision(False, "exec 不用于文件写入；请使用 write_file 工具。", "high")
    if _looks_like_long_running_server(lowered):
        return CommandPolicyDecision(
            False,
            "命令疑似启动长跑本地服务（如 flask/uvicorn/npm start/manage.py runserver 等）。"
            "exec_command 只用于短时命令；请改用 exec_command_background 后台运行，或让用户手动启动。",
            "medium",
        )
    if _looks_like_file_read_command(lowered):
        return CommandPolicyDecision(True, "命令像只读终端查询；允许短时执行。", "low")
    return CommandPolicyDecision(True, "命令未命中高风险规则；允许短时执行。", "medium")


def _normalize_command(command: str) -> str:
    """清理模型常见的 Markdown 包裹，避免把反引号当作命令字符。"""

    text = str(command or "").strip()
    if text.startswith("```") and text.endswith("```"):
        text = "\n".join(text.splitlines()[1:-1]).strip()
    return text.strip("` \t\r\n")


def _safe_subprocess_env() -> dict[str, str]:
    """构造子进程环境。

    保留 PATH 等基础环境，移除常见 API Key，防止模型通过 `env` 类命令直接把密钥打出来。
    这不是完整沙箱，但能降低误泄露风险。
    """

    blocked_markers = ("KEY", "TOKEN", "SECRET", "PASSWORD", "DASHSCOPE", "DEEPSEEK", "OPENAI")
    env = dict(os.environ)
    for key in list(env):
        upper = key.upper()
        if any(marker in upper for marker in blocked_markers):
            env.pop(key, None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.setdefault("LANG", "C.UTF-8")
    env.setdefault("LC_ALL", "C.UTF-8")
    return env


def _spawn_subprocess(command: str, *, cwd: Path) -> subprocess.Popen:
    """启动子进程并把它放到独立的进程组，便于超时后整树清理。

    Windows: CREATE_NEW_PROCESS_GROUP 让我们能向整组发 CTRL_BREAK；CREATE_NO_WINDOW
    避免短命令弹出黑框。
    POSIX: start_new_session=True 让 os.killpg 能命中孙子进程。
    """

    kwargs: dict[str, Any] = dict(
        cwd=cwd,
        shell=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=_safe_subprocess_env(),
    )
    if _IS_WINDOWS:
        creationflags = 0
        # 这些常量在 subprocess 模块中已定义，但仍按位 OR 以兼容旧 Python 版本。
        creationflags |= getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        creationflags |= getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
        kwargs["creationflags"] = creationflags
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(command, **kwargs)


def _terminate_process_tree(proc: subprocess.Popen) -> None:
    """超时/异常时清理子进程及其孙子进程。

    Windows 上 Flask reloader、npm 等会派生 grandchild。`proc.kill()` 只杀直接子进程，
    会留下 zombie 占住端口。这里改用 `taskkill /F /T` 整树清理；POSIX 走 killpg。
    出错时静默兜底——清理失败不应阻塞主流程。
    """

    if proc.poll() is not None:
        return
    pid = proc.pid
    try:
        if _IS_WINDOWS:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                timeout=5,
                check=False,
            )
        else:
            try:
                pgid = os.getpgid(pid)
                os.killpg(pgid, signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                pass
            try:
                proc.wait(timeout=1.5)
            except subprocess.TimeoutExpired:
                try:
                    pgid = os.getpgid(pid)
                    os.killpg(pgid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    pass
    except Exception:
        pass
    finally:
        # 最后一道保险，避免 Popen 句柄泄漏。
        try:
            proc.kill()
        except Exception:
            pass


def _has_background_operator(command: str) -> bool:
    return bool(re.search(r"(^|[^&])&($|\s)", command)) or "start-job" in command


def _looks_interactive(command: str) -> bool:
    patterns = [
        r"\bread-host\b",
        r"\bpause\b",
        r"\bgit\s+(?:rebase|add)\s+-i\b",
        r"\bnano\b",
        r"\bvim?\b",
        r"\bemacs\b",
        r"\bssh\b",
        r"^\s*(?:scp|sftp|plink|pscp)\b",
        r"\bftp\b",
        r"\bmysql\b",
        r"\bpsql\b",
    ]
    return any(re.search(pattern, command) for pattern in patterns)


def _looks_like_download_and_execute(command: str) -> bool:
    download = any(token in command for token in ["curl ", "wget ", "invoke-webrequest", "iwr ", "irm ", "invoke-restmethod"])
    execute = any(token in command for token in ["| sh", "| bash", "iex", "invoke-expression", "python -c", "node -e", "powershell -enc"])
    return download and execute


def _contains_dangerous_command(command: str) -> bool:
    dangerous_patterns = [
        r"\brm\s+-rf\b",
        r"\bdel\s+/(?:s|q)\b",
        r"\brmdir\s+/(?:s|q)\b",
        r"\bremove-item\b.*\b(?:-recurse|-force)\b",
        r"\bformat\b",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bhalt\b",
        r"\bsudo\b",
        r"\bsu\b",
        r"\bpkexec\b",
        r"\bchmod\s+-r\b",
        r"\bchown\s+-r\b",
        r"\bgit\s+reset\s+--hard\b",
        r"\bgit\s+clean\s+-f\b",
        r"\bgit\s+push\b.*\b--force\b",
    ]
    return any(re.search(pattern, command) for pattern in dangerous_patterns)


def _contains_shell_redirection(command: str) -> bool:
    """拦截会写入文件的 shell 重定向。

    允许管道 `|`，但不允许 `>`/`>>` 这类写文件能力；stderr 合并 `2>&1` 也先拦截，
    因为第一版没有完整 shell parser，保守一点更合适。
    """

    return bool(re.search(r"(^|[^<])>{1,2}[^&]", command)) or "2>&1" in command


def _looks_like_file_write_command(command: str) -> bool:
    write_patterns = [
        r"\bset-content\b",
        r"\bout-file\b",
        r"\bnew-item\b",
        r"\btouch\b",
        r"\bmkdir\b",
        r"\bcp\b",
        r"\bcopy\b",
        r"\bcopy-item\b",
        r"\bmv\b",
        r"\bmove-item\b",
    ]
    return any(re.search(pattern, command) for pattern in write_patterns)


def _looks_like_file_read_command(command: str) -> bool:
    read_commands = [
        "ls",
        "dir",
        "pwd",
        "git status",
        "git diff",
        "git log",
        "python --version",
        "python -v",
        "node --version",
        "npm --version",
        "cargo --version",
        "pytest --version",
    ]
    return any(command == item or command.startswith(f"{item} ") for item in read_commands)


def _looks_like_long_running_server(command: str) -> bool:
    """识别会占住前台不返回的本地服务启动命令。

    这种命令必须走 exec_command_background 才不会卡死 agent 循环。
    用相对宽松的正则——宁可误伤，也不要让模型反复触发长跑命令。
    """

    patterns = [
        r"\bflask\s+run\b",
        r"\buvicorn\b",
        r"\bgunicorn\b",
        r"\bhypercorn\b",
        r"\bdaphne\b",
        r"\bwaitress-serve\b",
        r"\bcelery\s+(?:worker|beat|--?\w*)\b",
        r"\bmanage\.py\s+runserver\b",
        r"\bpython\s+-m\s+http\.server\b",
        r"\bpython\s+-m\s+flask\b",
        r"\bpython\s+-m\s+uvicorn\b",
        r"\bpython\s+(?:[^|&;]*?[/\\])?(?:app|server|main|manage|wsgi|asgi|run|api)\.py\b",
        r"\bnode\s+(?:[^|&;]*?[/\\])?(?:server|app|index|main)\.(?:js|mjs|cjs|ts)\b",
        r"\b(?:npm|pnpm|yarn)\s+(?:run\s+)?(?:start|dev|serve|preview|watch)\b",
        r"\bnpx\s+(?:next|vite|nuxt|astro|remix|webpack|webpack-dev-server)\b",
        r"\b(?:next|vite|nuxt|astro|remix)\s+(?:dev|start|preview|serve)\b",
        r"\bdocker\s+compose\s+up\b(?!.*(?:\s|^)(?:-d|--detach)(?:\s|$))",
        r"\bdocker-compose\s+up\b(?!.*(?:\s|^)(?:-d|--detach)(?:\s|$))",
        r"\bdocker\s+run\b(?!.*(?:\s|^)(?:-d|--detach)(?:\s|$))",
        r"\bcargo\s+(?:run|watch)\b",
        r"\bdotnet\s+run\b",
        r"\bgo\s+run\b",
        r"\brails\s+s(?:erver)?\b",
        r"\bphp\s+-s(?:\s|$)",
    ]
    return any(re.search(pattern, command) for pattern in patterns)


def _truncate_outputs(stdout: str, stderr: str, *, max_output_bytes: int) -> tuple[str, str, bool]:
    combined = f"{stdout}\n{stderr}".encode("utf-8", errors="replace")
    if len(combined) <= max_output_bytes:
        return stdout, stderr, False
    stdout_budget = max_output_bytes // 2
    stderr_budget = max_output_bytes - stdout_budget
    return (
        _truncate_text_bytes(stdout, stdout_budget),
        _truncate_text_bytes(stderr, stderr_budget),
        True,
    )


def _strip_ansi(text: str) -> str:
    """清理终端颜色/光标控制码，避免模型把 ANSI escape 当成真实输出。"""

    ansi_pattern = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    return ansi_pattern.sub("", text or "")


def _truncate_text_bytes(text: str, max_bytes: int) -> str:
    encoded = text.encode("utf-8", errors="replace")
    if len(encoded) <= max_bytes:
        return text
    return encoded[:max_bytes].decode("utf-8", errors="ignore") + "\n...[output truncated]"


def _decode_timeout_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _error(
    error_code: str,
    message: str,
    *,
    blocked: bool = False,
    data: dict[str, Any] | None = None,
) -> ToolResult:
    return ToolResult(
        ok=False,
        tool_name="exec_command",
        data=data or {},
        error_code=error_code,
        message=message,
        blocked=blocked,
    )


def _policy_error_message(error_code: str) -> str:
    messages = {
        "PATH_CONTAINS_NULL_BYTE": "cwd 包含非法空字节，已拒绝执行。",
        "DEVICE_PATH_BLOCKED": "cwd 指向系统设备或进程文件，已拒绝执行。",
        "UNC_PATH_BLOCKED": "暂不允许在 UNC 网络路径中执行命令。",
        "PATH_OUTSIDE_WORKSPACE": "cwd 不在授权 workspace 内。",
    }
    return messages.get(error_code, "cwd 不在授权 workspace 内。")
