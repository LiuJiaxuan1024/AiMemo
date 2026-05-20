from __future__ import annotations

from datetime import datetime, timezone
from difflib import get_close_matches
from fnmatch import fnmatch
import json
from pathlib import Path
from typing import Any, Iterable

from app.local_operator.policy import LocalOperatorPolicy
from app.local_operator.schemas import ToolResult


TEXT_EXTENSIONS = {
    ".py",
    ".ts",
    ".tsx",
    ".js",
    ".jsx",
    ".json",
    ".md",
    ".txt",
    ".css",
    ".html",
    ".yml",
    ".yaml",
    ".toml",
    ".ini",
    ".sql",
}

FAST_READ_LIMIT_BYTES = 10 * 1024 * 1024


class LocalFilesystemService:
    """read-only 文件系统服务。

    这个类只负责确定性文件操作，不知道 LangChain，也不写审计表。
    所有入口都先走 LocalOperatorPolicy，防止模型通过相对路径、软链接等方式逃逸。
    """

    def __init__(self, policy: LocalOperatorPolicy):
        self.policy = policy

    def list_dir(self, path: str, *, max_entries: int = 100, include_hidden: bool = False) -> ToolResult:
        try:
            resolved = self._resolve_existing(path)
        except LocalFilesystemError as exc:
            return _error("list_dir", exc.error_code, exc.message, blocked=exc.error_code == "PATH_OUTSIDE_WORKSPACE")
        if not resolved.is_dir():
            return _error("list_dir", "PATH_IS_FILE", "路径是文件，不是目录。")

        entries = []
        max_entries = min(max(max_entries, 1), 500)
        for child in sorted(resolved.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower())):
            if child.is_dir() and self.policy.should_skip_dir(child, include_hidden=include_hidden):
                continue
            if child.is_file() and self.policy.should_skip_file(child, include_hidden=include_hidden):
                continue
            entries.append(
                {
                    "name": child.name,
                    "relative_path": self.policy.relative_path(child),
                    "kind": "directory" if child.is_dir() else "file",
                    "size": None if child.is_dir() else child.stat().st_size,
                    "modified_at": _iso_mtime(child),
                }
            )
            if len(entries) >= max_entries:
                break

        return _ok(
            "list_dir",
            {
                "path": resolved.as_posix(),
                "relative_path": self.policy.relative_path(resolved),
                "entries": entries,
                "truncated": len(entries) >= max_entries,
            },
        )

    def read_file(
        self,
        path: str,
        *,
        start_line: int | None = None,
        end_line: int | None = None,
        max_bytes: int = 65536,
    ) -> ToolResult:
        try:
            resolved = self._resolve_existing(path)
        except LocalFilesystemError as exc:
            return _error("read_file", exc.error_code, exc.message, blocked=_is_policy_block(exc.error_code))
        if resolved.is_dir():
            return _error("read_file", "PATH_IS_DIRECTORY", "路径是目录，不是文件。")
        blocked = self._validate_readable_text_file("read_file", resolved)
        if blocked:
            return blocked

        line_start = max(int(start_line or 1), 1)
        requested_end = int(end_line) if end_line is not None else None
        if requested_end is not None and requested_end < line_start:
            return _error("read_file", "INVALID_ARGUMENT", "end_line 不能小于 start_line。")

        max_bytes = min(max(max_bytes, 1), self.policy.max_file_bytes)
        read_result = _read_text_file_in_range(
            resolved,
            line_start=line_start,
            line_end=requested_end,
            max_bytes=max_bytes,
        )

        return _ok(
            "read_file",
            {
                "path": resolved.as_posix(),
                "relative_path": self.policy.relative_path(resolved),
                "line_start": read_result["line_start"],
                "line_end": read_result["line_end"],
                "total_lines": read_result["total_lines"],
                "total_bytes": resolved.stat().st_size,
                "read_bytes": read_result["read_bytes"],
                "bytes_returned": read_result["bytes_returned"],
                "modified_at": _iso_mtime(resolved),
                "truncated": read_result["truncated"],
                "truncated_by_bytes": read_result["truncated_by_bytes"],
                "content": read_result["content"],
                "numbered_content": read_result["numbered_content"],
            },
        )

    def search_files(
        self,
        root: str,
        *,
        pattern: str,
        max_results: int = 50,
        include_hidden: bool = False,
    ) -> ToolResult:
        try:
            resolved_root = self._resolve_existing(root or ".")
        except LocalFilesystemError as exc:
            return _error("search_files", exc.error_code, exc.message, blocked=_is_policy_block(exc.error_code))
        if not resolved_root.is_dir():
            return _error("search_files", "PATH_IS_FILE", "搜索根路径必须是目录。")

        matches = []
        max_results = min(max(max_results, 1), 200)
        for path in self._walk_paths(resolved_root, include_hidden=include_hidden, include_root=True):
            if fnmatch(path.name, pattern) or pattern.lower() in path.name.lower():
                matches.append(
                    {
                        "relative_path": self.policy.relative_path(path),
                        "kind": "directory" if path.is_dir() else "file",
                        "size": None if path.is_dir() else path.stat().st_size,
                    }
                )
                if len(matches) >= max_results:
                    break

        return _ok(
            "search_files",
            {
                "root": resolved_root.as_posix(),
                "pattern": pattern,
                "matches": matches,
                "truncated": len(matches) >= max_results,
            },
        )

    def search_text(
        self,
        root: str,
        *,
        query: str,
        include_glob: str | None = None,
        max_results: int = 50,
        context_lines: int = 2,
    ) -> ToolResult:
        try:
            resolved_root = self._resolve_existing(root or ".")
        except LocalFilesystemError as exc:
            return _error("search_text", exc.error_code, exc.message, blocked=_is_policy_block(exc.error_code))
        if not resolved_root.is_dir():
            return _error("search_text", "PATH_IS_FILE", "搜索根路径必须是目录。")
        if not query.strip():
            return _error("search_text", "INVALID_ARGUMENT", "query 不能为空。")

        matches: list[dict[str, Any]] = []
        max_results = min(max(max_results, 1), 200)
        context_lines = min(max(context_lines, 0), 5)
        lowered_query = query.lower()
        for path in self._walk_files(resolved_root, include_hidden=False):
            if include_glob and not fnmatch(path.name, include_glob):
                continue
            if not self._is_text_file(path) or path.stat().st_size > self.policy.max_search_file_bytes:
                continue
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            for index, line in enumerate(lines, start=1):
                if lowered_query not in line.lower():
                    continue
                start = max(index - context_lines, 1)
                end = min(index + context_lines, len(lines))
                preview = "\n".join(lines[start - 1 : end])
                matches.append(
                    {
                        "relative_path": self.policy.relative_path(path),
                        "line": index,
                        "preview": preview,
                    }
                )
                if len(matches) >= max_results:
                    break
            if len(matches) >= max_results:
                break

        return _ok(
            "search_text",
            {
                "root": resolved_root.as_posix(),
                "query": query,
                "matches": matches,
                "match_count": len(matches),
                "truncated": len(matches) >= max_results,
            },
        )

    def get_file_info(self, path: str) -> ToolResult:
        try:
            resolved = self._resolve_existing(path)
        except LocalFilesystemError as exc:
            return _error("get_file_info", exc.error_code, exc.message, blocked=_is_policy_block(exc.error_code))
        stat = resolved.stat()
        return _ok(
            "get_file_info",
            {
                "path": resolved.as_posix(),
                "relative_path": self.policy.relative_path(resolved),
                "kind": "directory" if resolved.is_dir() else "file",
                "size": None if resolved.is_dir() else stat.st_size,
                "modified_at": _iso_mtime(resolved),
                "is_sensitive": self.policy.is_sensitive_path(resolved),
                "is_text": resolved.is_file() and self._is_text_file(resolved),
            },
        )

    def _resolve_existing(self, raw_path: str) -> Path:
        try:
            resolved = self.policy.resolve_authorized_path(raw_path)
        except PermissionError as exc:
            error_code = str(exc)
            raise LocalFilesystemError(error_code, _policy_error_message(error_code)) from exc
        if not resolved.exists():
            suggestion = self._suggest_existing_path(raw_path)
            message = "路径不存在。"
            if suggestion:
                message = f"路径不存在。你是不是想访问 `{suggestion}`？"
            raise LocalFilesystemError("PATH_NOT_FOUND", message)
        return resolved

    def _validate_readable_text_file(
        self,
        tool_name: str,
        path: Path,
    ) -> ToolResult | None:
        if self.policy.is_sensitive_path(path):
            return _error(tool_name, "SENSITIVE_FILE_BLOCKED", "该文件可能包含敏感信息，已拒绝读取。", blocked=True)
        if not self._is_text_file(path):
            return _error(tool_name, "BINARY_FILE_BLOCKED", "该文件不是受支持的文本文件。", blocked=True)
        return None

    def _walk_files(self, root: Path, *, include_hidden: bool) -> Iterable[Path]:
        """惰性遍历文件，避免大目录搜索时先把所有路径塞进内存。"""

        for child in self._walk_paths(root, include_hidden=include_hidden, include_root=False):
            if child.is_file():
                yield child

    def _walk_paths(self, root: Path, *, include_hidden: bool, include_root: bool) -> Iterable[Path]:
        """惰性遍历文件和目录。

        `search_files` 虽然沿用了旧名字，但用户问“有没有某个项目”时，目标通常是目录。
        因此这里同时产出目录和文件；真正读取正文的 `search_text` 仍只消费文件。
        """

        if include_root:
            yield root
        for child in root.rglob("*"):
            if any(self.policy.should_skip_dir(parent, include_hidden=include_hidden) for parent in child.parents if parent != root):
                continue
            if child.is_dir():
                if not self.policy.should_skip_dir(child, include_hidden=include_hidden):
                    yield child
                continue
            if child.is_file() and not self.policy.should_skip_file(child, include_hidden=include_hidden):
                yield child

    def _is_text_file(self, path: Path) -> bool:
        if path.suffix.lower() in TEXT_EXTENSIONS:
            return True
        try:
            sample = path.read_bytes()[:2048]
        except OSError:
            return False
        return b"\x00" not in sample

    def _suggest_existing_path(self, raw_path: str) -> str:
        """给路径不存在错误提供同目录近似文件名建议。"""

        try:
            candidate = self.policy.resolve_authorized_path(raw_path)
        except PermissionError:
            return ""
        parent = candidate.parent
        if not parent.exists() or not parent.is_dir():
            return ""
        target = candidate.name.lower()
        sibling_names = [child.name for child in parent.iterdir()]
        close_matches = get_close_matches(candidate.name, sibling_names, n=1, cutoff=0.45)
        if close_matches:
            return self.policy.relative_path(parent / close_matches[0])
        for child in parent.iterdir():
            if child.name.lower() == target or target in child.name.lower() or child.name.lower() in target:
                return self.policy.relative_path(child)
        return ""


class LocalFilesystemError(Exception):
    """文件系统服务中的可预期错误。"""

    def __init__(self, error_code: str, message: str):
        super().__init__(message)
        self.error_code = error_code
        self.message = message


def _ok(tool_name: str, data: dict[str, Any]) -> ToolResult:
    return ToolResult(ok=True, tool_name=tool_name, data=data)


def _error(tool_name: str, error_code: str, message: str, *, blocked: bool = False) -> ToolResult:
    return ToolResult(ok=False, tool_name=tool_name, error_code=error_code, message=message, blocked=blocked)


def _iso_mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc).isoformat()


def tool_result_to_json(result: ToolResult) -> str:
    """把工具结果转换为 LangChain 工具需要的字符串输出。"""

    return json.dumps(result.model_dump(), ensure_ascii=False)


def _policy_error_message(error_code: str) -> str:
    messages = {
        "PATH_CONTAINS_NULL_BYTE": "路径包含非法空字节，已拒绝读取。",
        "DEVICE_PATH_BLOCKED": "该路径指向系统设备或进程文件，已拒绝读取。",
        "UNC_PATH_BLOCKED": "暂不读取 UNC 网络路径，避免泄露系统凭据。",
        "PATH_OUTSIDE_WORKSPACE": "路径不在授权 workspace 内。",
    }
    return messages.get(error_code, "路径不在授权 workspace 内。")


def _is_policy_block(error_code: str) -> bool:
    return error_code in {
        "PATH_OUTSIDE_WORKSPACE",
        "PATH_CONTAINS_NULL_BYTE",
        "DEVICE_PATH_BLOCKED",
        "UNC_PATH_BLOCKED",
    }


def _read_text_file_in_range(
    path: Path,
    *,
    line_start: int,
    line_end: int | None,
    max_bytes: int,
) -> dict[str, Any]:
    """读取文本文件的指定行范围，并限制返回字节数。

    设计上参考 Claude Code 的 `readFileInRange`：
    - 小文件走快速路径，直接读入后切行。
    - 大文件走逐行扫描，只保留用户请求范围，避免为几行内容加载整份文件。
    - 统一去掉 UTF BOM，并把 CRLF 规范化成 LF，减少跨平台差异。

    参数：
      path: 已通过权限检查的文件路径。
      line_start: 1-based 起始行。
      line_end: 1-based 结束行，包含该行；为空表示读到文件末尾或字节上限。
      max_bytes: 本次最多返回的 UTF-8 字节数。
    """

    stat_size = path.stat().st_size
    if stat_size <= FAST_READ_LIMIT_BYTES:
        content = _decode_text_bytes(path.read_bytes())
        lines = [] if content == "" else content.split("\n")
        return _select_lines(lines, line_start=line_start, line_end=line_end, max_bytes=max_bytes, read_bytes=stat_size)

    selected: list[str] = []
    total_lines = 0
    returned_bytes = 0
    truncated_by_bytes = False
    actual_end = line_start - 1
    encoding = _detect_encoding(path)

    with path.open("r", encoding=encoding, errors="replace", newline=None) as file:
        for raw_line in file:
            total_lines += 1
            if total_lines < line_start:
                continue
            if line_end is not None and total_lines > line_end:
                continue
            line = raw_line.rstrip("\n").rstrip("\r")
            line_bytes = len((line + ("\n" if selected else "")).encode("utf-8"))
            if returned_bytes + line_bytes > max_bytes:
                remaining = max(max_bytes - returned_bytes, 0)
                if remaining > 0:
                    prefix = ("\n" if selected else "") + line
                    selected.append(prefix.encode("utf-8")[:remaining].decode("utf-8", errors="ignore").lstrip("\n"))
                truncated_by_bytes = True
                actual_end = total_lines
                # 继续数总行数，但不再保留内容。
                continue
            selected.append(line)
            returned_bytes += line_bytes
            actual_end = total_lines

    content = "\n".join(selected)
    return {
        "line_start": min(line_start, total_lines) if total_lines else 1,
        "line_end": actual_end if selected else min(line_start - 1, total_lines),
        "total_lines": total_lines,
        "read_bytes": stat_size,
        "bytes_returned": len(content.encode("utf-8")),
        "truncated": truncated_by_bytes or (line_end is not None and line_end < total_lines),
        "truncated_by_bytes": truncated_by_bytes,
        "content": content,
        "numbered_content": _add_line_numbers(content, line_start),
    }


def _select_lines(
    lines: list[str],
    *,
    line_start: int,
    line_end: int | None,
    max_bytes: int,
    read_bytes: int,
) -> dict[str, Any]:
    total_lines = len(lines)
    actual_start = min(line_start, total_lines) if total_lines else 1
    actual_end = min(line_end if line_end is not None else total_lines, total_lines)
    selected = lines[actual_start - 1 : actual_end] if actual_end >= actual_start else []
    content = "\n".join(selected)
    encoded = content.encode("utf-8")
    truncated_by_bytes = len(encoded) > max_bytes
    if truncated_by_bytes:
        content = encoded[:max_bytes].decode("utf-8", errors="ignore")
        actual_end = actual_start + max(content.count("\n"), 0)
    return {
        "line_start": actual_start,
        "line_end": actual_end,
        "total_lines": total_lines,
        "read_bytes": read_bytes,
        "bytes_returned": len(content.encode("utf-8")),
        "truncated": truncated_by_bytes or (line_end is not None and line_end < total_lines),
        "truncated_by_bytes": truncated_by_bytes,
        "content": content,
        "numbered_content": _add_line_numbers(content, actual_start),
    }


def _decode_text_bytes(raw: bytes) -> str:
    """解码文本并处理常见 BOM。"""

    if raw.startswith(b"\xff\xfe"):
        return raw.decode("utf-16-le", errors="replace").lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    if raw.startswith(b"\xef\xbb\xbf"):
        raw = raw[3:]
    return raw.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def _detect_encoding(path: Path) -> str:
    sample = path.read_bytes()[:4]
    if sample.startswith(b"\xff\xfe"):
        return "utf-16-le"
    return "utf-8-sig"


def _add_line_numbers(content: str, start_line: int) -> str:
    """生成给模型/调试面板看的带行号版本。"""

    if not content:
        return ""
    return "\n".join(f"{line_number:>6}\t{line}" for line_number, line in enumerate(content.split("\n"), start=start_line))
