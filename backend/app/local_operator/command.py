from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any

from app.local_operator.policy import LocalOperatorPolicy
from app.local_operator.schemas import ToolResult


DEFAULT_EXEC_TIMEOUT_MS = 30_000
MAX_EXEC_TIMEOUT_MS = 120_000
DEFAULT_MAX_OUTPUT_BYTES = 64 * 1024
MAX_OUTPUT_BYTES = 256 * 1024


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
        try:
            completed = subprocess.run(
                normalized_command,
                cwd=resolved_cwd,
                shell=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                capture_output=True,
                timeout=timeout_ms / 1000,
                env=_safe_subprocess_env(),
            )
            exit_code = int(completed.returncode)
            stdout = completed.stdout or ""
            stderr = completed.stderr or ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            exit_code = -1
            stdout = _decode_timeout_output(exc.stdout)
            stderr = _decode_timeout_output(exc.stderr) or f"命令执行超过 {timeout_ms}ms，已终止。"
        except OSError as exc:
            return _error("EXEC_FAILED", f"命令启动失败：{exc}")

        stdout, stderr, truncated = _truncate_outputs(stdout, stderr, max_output_bytes=max_output_bytes)
        # 子进程成功启动不等于命令成功。agent 的重规划依赖 ok=false 来识别失败，
        # 因此非 0 退出码必须作为工具失败回传，同时保留 stdout/stderr 供后续判断。
        failed_with_exit_code = (not timed_out) and exit_code != 0
        ok = (not timed_out) and (not failed_with_exit_code)
        error_code = "COMMAND_TIMEOUT" if timed_out else ("COMMAND_EXITED_NON_ZERO" if failed_with_exit_code else "")
        message = "命令超时，已终止。" if timed_out else ("命令以非 0 状态退出。" if failed_with_exit_code else "")
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
                "truncated": truncated,
                "risk_level": decision.risk_level,
            },
            error_code=error_code,
            message=message,
            blocked=False,
        )


def evaluate_command_policy(command: str) -> CommandPolicyDecision:
    """判断命令是否允许执行。

    这里借鉴 Claude Code 的 Bash/PowerShell 思路，但第一版不尝试完整解析 shell AST。
    解析不完整时要保守：命中危险符号或危险命令就拦截，普通开发命令先允许短时执行。
    """

    lowered = command.lower().strip()
    if _has_background_operator(lowered):
        return CommandPolicyDecision(False, "暂不支持后台命令或后台运算符。", "high")
    if _looks_interactive(lowered):
        return CommandPolicyDecision(False, "暂不支持交互式命令。", "high")
    if _looks_like_download_and_execute(lowered):
        return CommandPolicyDecision(False, "命令疑似下载后执行远程代码。", "high")
    if _contains_dangerous_command(lowered):
        return CommandPolicyDecision(False, "命令包含删除、格式化、关机、权限提升或破坏性操作。", "high")
    if _contains_shell_redirection(lowered):
        return CommandPolicyDecision(False, "exec 暂不允许 shell 重定向写文件；写文件请使用 write_file。", "high")
    if _looks_like_file_write_command(lowered):
        return CommandPolicyDecision(False, "exec 不用于文件写入；请使用 write_file 工具。", "high")
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
    return env


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
