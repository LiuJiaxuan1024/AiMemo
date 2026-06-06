from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any
from urllib import error as urllib_error
from urllib import request

from app.core.config import settings
from app.local_operator.policy import LocalOperatorPolicy
from app.local_operator.schemas import ToolResult


MAX_REMOTE_TIMEOUT_MS = settings.local_operator_exec_max_timeout_ms


@dataclass(frozen=True)
class RemoteTarget:
    host: str
    username: str
    port: int = 22
    identity_file: Path | None = None
    connect_timeout_seconds: int = 10


class RemoteOperatorService:
    """Non-interactive SSH/SCP helpers for remote deployment tasks.

    The first version deliberately supports only SSH key / existing SSH agent flows.
    Password prompts are classified as INTERACTIVE_AUTH_REQUIRED instead of hanging
    inside generic exec_command.
    """

    def __init__(self, policy: LocalOperatorPolicy):
        self.policy = policy

    def connectivity_check(
        self,
        *,
        host: str,
        username: str,
        port: int = 22,
        identity_file: str | None = None,
        connect_timeout_seconds: int = 10,
    ) -> ToolResult:
        target = self._target(
            host=host,
            username=username,
            port=port,
            identity_file=identity_file,
            connect_timeout_seconds=connect_timeout_seconds,
        )
        if isinstance(target, ToolResult):
            return _with_tool_name(target, "remote_connectivity_check")
        ssh_path = shutil.which("ssh")
        if not ssh_path:
            return _error(
                "LOCAL_SSH_NOT_FOUND",
                "本机未找到 ssh 命令，无法进行远程连接检查。",
                tool_name="remote_connectivity_check",
            )
        args = [
            ssh_path,
            *_ssh_options(target),
            _destination(target),
            "printf AIMEMO_REMOTE_OK",
        ]
        result = _run_process(args, timeout_ms=max(5_000, target.connect_timeout_seconds * 1000 + 2_000))
        if result.ok and "AIMEMO_REMOTE_OK" in str(result.data.get("stdout") or ""):
            result.data.update(_target_data(target))
            result.data["status"] = "reachable"
            return _with_tool_name(result, "remote_connectivity_check")
        return _classify_remote_failure(result, tool_name="remote_connectivity_check", target=target)

    def upload_file(
        self,
        *,
        host: str,
        username: str,
        local_path: str,
        remote_path: str,
        port: int = 22,
        identity_file: str | None = None,
        connect_timeout_seconds: int = 10,
    ) -> ToolResult:
        target = self._target(
            host=host,
            username=username,
            port=port,
            identity_file=identity_file,
            connect_timeout_seconds=connect_timeout_seconds,
        )
        if isinstance(target, ToolResult):
            return _with_tool_name(target, "remote_upload_file")
        if not remote_path.startswith("/"):
            return _error(
                "REMOTE_PATH_NOT_ABSOLUTE",
                "remote_path 必须是远程绝对路径。",
                blocked=True,
                tool_name="remote_upload_file",
            )
        try:
            resolved_local = self.policy.resolve_authorized_path(local_path)
        except PermissionError as exc:
            return _error(str(exc), "local_path 不在授权 workspace 内。", blocked=True, tool_name="remote_upload_file")
        if not resolved_local.exists():
            return _error("LOCAL_PATH_NOT_FOUND", "要上传的本地文件不存在。", tool_name="remote_upload_file")
        if not resolved_local.is_file():
            return _error("LOCAL_PATH_NOT_FILE", "remote_upload_file 只能上传单个文件。", tool_name="remote_upload_file")
        scp_path = shutil.which("scp")
        if not scp_path:
            return _error("LOCAL_SCP_NOT_FOUND", "本机未找到 scp 命令，无法上传文件。", tool_name="remote_upload_file")
        args = [
            scp_path,
            "-P",
            str(target.port),
            *_scp_options(target),
            str(resolved_local),
            f"{_destination(target)}:{remote_path}",
        ]
        result = _run_process(args, timeout_ms=MAX_REMOTE_TIMEOUT_MS)
        result.data.update(
            {
                **_target_data(target),
                "local_path": resolved_local.as_posix(),
                "remote_path": remote_path,
            }
        )
        if result.ok:
            return _with_tool_name(result, "remote_upload_file")
        return _classify_remote_failure(result, tool_name="remote_upload_file", target=target)

    def exec(
        self,
        *,
        host: str,
        username: str,
        command: str,
        port: int = 22,
        identity_file: str | None = None,
        connect_timeout_seconds: int = 10,
        timeout_ms: int = settings.local_operator_exec_default_timeout_ms,
    ) -> ToolResult:
        target = self._target(
            host=host,
            username=username,
            port=port,
            identity_file=identity_file,
            connect_timeout_seconds=connect_timeout_seconds,
        )
        if isinstance(target, ToolResult):
            return _with_tool_name(target, "remote_exec")
        normalized = str(command or "").strip()
        if not normalized:
            return _error("INVALID_ARGUMENT", "remote_exec.command 不能为空。", blocked=True, tool_name="remote_exec")
        if _looks_dangerous_remote_command(normalized):
            return _error(
                "REMOTE_COMMAND_BLOCKED",
                "远程命令包含删除、权限提升、关机或明显破坏性操作。",
                blocked=True,
                tool_name="remote_exec",
            )
        ssh_path = shutil.which("ssh")
        if not ssh_path:
            return _error("LOCAL_SSH_NOT_FOUND", "本机未找到 ssh 命令，无法执行远程命令。", tool_name="remote_exec")
        args = [
            ssh_path,
            *_ssh_options(target),
            _destination(target),
            normalized,
        ]
        result = _run_process(args, timeout_ms=timeout_ms)
        result.data.update({**_target_data(target), "remote_command": normalized})
        if result.ok:
            return _with_tool_name(result, "remote_exec")
        return _classify_remote_failure(result, tool_name="remote_exec", target=target)

    def verify_http(self, *, url: str, expected_text: str | None = None, timeout_seconds: int = 10) -> ToolResult:
        if not re.match(r"^https?://", str(url or ""), flags=re.I):
            return _error("INVALID_URL", "remote_verify_http.url 必须以 http:// 或 https:// 开头。", blocked=True)
        started_at = time.perf_counter()
        try:
            with request.urlopen(str(url), timeout=timeout_seconds) as response:
                body = response.read(200_000).decode("utf-8", errors="replace")
                status_code = int(getattr(response, "status", 0) or 0)
        except urllib_error.HTTPError as exc:
            return ToolResult(
                ok=False,
                tool_name="remote_verify_http",
                data={"url": url, "status_code": exc.code, "duration_ms": int((time.perf_counter() - started_at) * 1000)},
                error_code="HTTP_STATUS_ERROR",
                message=f"HTTP 请求返回状态码 {exc.code}。",
            )
        except Exception as exc:
            return ToolResult(
                ok=False,
                tool_name="remote_verify_http",
                data={"url": url, "duration_ms": int((time.perf_counter() - started_at) * 1000)},
                error_code="HTTP_VERIFY_FAILED",
                message=str(exc),
            )
        contains_expected = True if not expected_text else expected_text in body
        return ToolResult(
            ok=contains_expected,
            tool_name="remote_verify_http",
            data={
                "url": url,
                "status_code": status_code,
                "contains_expected_text": contains_expected,
                "response_preview": body[:1000],
                "duration_ms": int((time.perf_counter() - started_at) * 1000),
            },
            error_code="" if contains_expected else "EXPECTED_TEXT_NOT_FOUND",
            message="" if contains_expected else "HTTP 响应中没有找到 expected_text。",
        )

    def _target(
        self,
        *,
        host: str,
        username: str,
        port: int,
        identity_file: str | None,
        connect_timeout_seconds: int,
    ) -> RemoteTarget | ToolResult:
        host = str(host or "").strip()
        username = str(username or "").strip()
        if not host or "://" in host or "@" in host:
            return _error("INVALID_REMOTE_HOST", "host 只能是主机名或 IP，不要包含协议或用户名。", blocked=True)
        if not username:
            return _error("INVALID_REMOTE_USER", "username 不能为空。", blocked=True)
        identity_path = None
        if identity_file:
            try:
                identity_path = self.policy.resolve_authorized_path(identity_file)
            except PermissionError:
                return _error("IDENTITY_FILE_OUTSIDE_WORKSPACE", "identity_file 不在授权 workspace 内。", blocked=True)
            if not identity_path.exists() or not identity_path.is_file():
                return _error("IDENTITY_FILE_NOT_FOUND", "指定的 SSH 私钥文件不存在。")
        return RemoteTarget(
            host=host,
            username=username,
            port=int(port or 22),
            identity_file=identity_path,
            connect_timeout_seconds=int(connect_timeout_seconds or 10),
        )


def _ssh_options(target: RemoteTarget) -> list[str]:
    options = [
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"ConnectTimeout={target.connect_timeout_seconds}",
        "-p",
        str(target.port),
    ]
    if target.identity_file is not None:
        options.extend(["-i", str(target.identity_file)])
    return options


def _scp_options(target: RemoteTarget) -> list[str]:
    options = [
        "-o",
        "BatchMode=yes",
        "-o",
        "StrictHostKeyChecking=accept-new",
        "-o",
        f"ConnectTimeout={target.connect_timeout_seconds}",
    ]
    if target.identity_file is not None:
        options.extend(["-i", str(target.identity_file)])
    return options


def _destination(target: RemoteTarget) -> str:
    return f"{target.username}@{target.host}"


def _target_data(target: RemoteTarget) -> dict[str, Any]:
    return {
        "host": target.host,
        "username": target.username,
        "port": target.port,
        "identity_file": target.identity_file.as_posix() if target.identity_file else None,
    }


def _run_process(args: list[str], *, timeout_ms: int) -> ToolResult:
    started_at = time.perf_counter()
    timeout_ms = min(max(int(timeout_ms or 1_000), 1_000), MAX_REMOTE_TIMEOUT_MS)
    try:
        completed = subprocess.run(
            args,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_ms / 1000,
            check=False,
        )
        stdout = _strip_ansi(completed.stdout or "")
        stderr = _strip_ansi(completed.stderr or "")
        exit_code = int(completed.returncode)
        ok = exit_code == 0
        return ToolResult(
            ok=ok,
            tool_name="remote_process",
            data={
                "command": _display_args(args),
                "exit_code": exit_code,
                "stdout": stdout[-8000:],
                "stderr": stderr[-8000:],
                "duration_ms": int((time.perf_counter() - started_at) * 1000),
                "timed_out": False,
            },
            error_code="" if ok else "REMOTE_PROCESS_EXITED_NON_ZERO",
            message="" if ok else "远程进程命令以非 0 状态退出。",
        )
    except subprocess.TimeoutExpired as exc:
        stdout = _decode_timeout_output(exc.stdout)
        stderr = _decode_timeout_output(exc.stderr) or f"远程命令执行超过 {timeout_ms}ms，已终止。"
        return ToolResult(
            ok=False,
            tool_name="remote_process",
            data={
                "command": _display_args(args),
                "exit_code": -1,
                "stdout": _strip_ansi(stdout)[-8000:],
                "stderr": _strip_ansi(stderr)[-8000:],
                "duration_ms": int((time.perf_counter() - started_at) * 1000),
                "timed_out": True,
            },
            error_code="REMOTE_PROCESS_TIMEOUT",
            message="远程命令超时，可能在等待交互式认证或远程命令未返回。",
        )
    except FileNotFoundError as exc:
        return _error("LOCAL_REMOTE_COMMAND_NOT_FOUND", f"本机缺少远程操作命令：{exc.filename}")


def _classify_remote_failure(result: ToolResult, *, tool_name: str, target: RemoteTarget) -> ToolResult:
    text = f"{result.data.get('stdout', '')}\n{result.data.get('stderr', '')}".lower()
    code = str(result.error_code or "")
    message = str(result.message or "")
    blocked = False
    if _looks_like_auth_problem(text, timed_out=bool(result.data.get("timed_out"))):
        code = "INTERACTIVE_AUTH_REQUIRED"
        message = "远程 SSH/SCP 需要交互式认证或未配置可用 SSH key；当前工具不会输入密码。"
        blocked = True
    elif "could not resolve hostname" in text or "name or service not known" in text:
        code = "REMOTE_HOST_UNRESOLVED"
        message = "无法解析远程主机名。"
    elif "connection refused" in text:
        code = "REMOTE_CONNECTION_REFUSED"
        message = "远程 SSH 端口拒绝连接。"
    elif "connection timed out" in text or result.data.get("timed_out"):
        code = "REMOTE_CONNECTION_TIMEOUT"
        message = "远程连接或命令执行超时。"
    elif "no such file or directory" in text:
        code = "REMOTE_PATH_NOT_FOUND"
        message = "远程路径或本地路径不存在。"
    return ToolResult(
        ok=False,
        tool_name=tool_name,
        data={**dict(result.data or {}), **_target_data(target)},
        error_code=code,
        message=message,
        blocked=blocked,
    )


def _with_tool_name(result: ToolResult, tool_name: str) -> ToolResult:
    return ToolResult(
        ok=result.ok,
        tool_name=tool_name,
        data=dict(result.data or {}),
        error_code=result.error_code,
        message=result.message,
        blocked=result.blocked,
    )


def _looks_like_auth_problem(text: str, *, timed_out: bool) -> bool:
    markers = [
        "permission denied",
        "publickey",
        "password",
        "passphrase",
        "keyboard-interactive",
        "host key verification failed",
        "the authenticity of host",
        "too many authentication failures",
    ]
    if any(marker in text for marker in markers):
        return True
    return timed_out and "authorized users only" in text


def _looks_dangerous_remote_command(command: str) -> bool:
    lowered = command.lower()
    patterns = [
        r"\brm\s+-rf\b",
        r"\bmkfs\b",
        r"\bdd\s+if=",
        r"\bshutdown\b",
        r"\breboot\b",
        r"\bhalt\b",
        r"\bsudo\b",
        r"\bsu\b",
        r"\bchmod\s+-r\b",
        r"\bchown\s+-r\b",
    ]
    return any(re.search(pattern, lowered) for pattern in patterns)


def _strip_ansi(text: str) -> str:
    ansi_pattern = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")
    return ansi_pattern.sub("", text or "")


def _display_args(args: list[str]) -> str:
    return " ".join(_quote_arg(arg) for arg in args)


def _quote_arg(arg: str) -> str:
    if re.search(r"\s", arg):
        return '"' + arg.replace('"', '\\"') + '"'
    return arg


def _decode_timeout_output(value: bytes | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    return str(value)


def _error(error_code: str, message: str, *, blocked: bool = False, tool_name: str = "remote_operator") -> ToolResult:
    return ToolResult(ok=False, tool_name=tool_name, error_code=error_code, message=message, blocked=blocked)
