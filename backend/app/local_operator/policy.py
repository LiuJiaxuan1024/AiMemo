from dataclasses import dataclass
import os
from pathlib import Path
import re
import unicodedata


SENSITIVE_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.development",
    ".env.production",
    "id_rsa",
    "id_ed25519",
}
SENSITIVE_SUFFIXES = {".key", ".pem", ".p12", ".pfx", ".sqlite", ".db"}
IGNORED_DIR_NAMES = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".pytest_cache",
    "dist",
    "build",
}


@dataclass(frozen=True)
class LocalOperatorPolicy:
    """本地操作权限策略。

    参数：
      workspace_roots: 允许读取的根目录。所有工具路径都必须落在这些目录内。
      max_file_bytes: 单次 read_file 允许返回的最大字节数。
      max_search_file_bytes: search_text 扫描单个文本文件的最大字节数。
    """

    workspace_roots: tuple[Path, ...]
    max_file_bytes: int = 128 * 1024
    max_search_file_bytes: int = 512 * 1024

    @classmethod
    def from_roots(cls, roots: list[str] | tuple[str, ...] | None) -> "LocalOperatorPolicy":
        raw_roots = list(roots or ["."])
        normalized = tuple(Path(root).expanduser().resolve() for root in raw_roots)
        return cls(workspace_roots=normalized)

    def resolve_authorized_path(self, raw_path: str) -> Path:
        """把用户/模型传入路径规范化，并确保没有逃出授权 workspace。"""

        normalized_path = _normalize_user_path(raw_path or ".")
        candidate = Path(normalized_path).expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace_roots[0] / candidate
        resolved = candidate.resolve()
        if not any(_is_relative_to(resolved, root) for root in self.workspace_roots):
            raise PermissionError("PATH_OUTSIDE_WORKSPACE")
        return resolved

    def relative_path(self, path: Path) -> str:
        """返回用于展示和传给模型的 workspace 相对路径。"""

        resolved = path.resolve()
        for root in self.workspace_roots:
            if _is_relative_to(resolved, root):
                return resolved.relative_to(root).as_posix() or "."
        return resolved.as_posix()

    def is_sensitive_path(self, path: Path) -> bool:
        """判断路径是否命中敏感文件规则。"""

        name = path.name.lower()
        if name in SENSITIVE_FILE_NAMES:
            return True
        return path.suffix.lower() in SENSITIVE_SUFFIXES

    def should_skip_dir(self, path: Path, *, include_hidden: bool = False) -> bool:
        """判断搜索/列目录时是否跳过目录。"""

        name = path.name
        if name in IGNORED_DIR_NAMES:
            return True
        return not include_hidden and name.startswith(".")

    def should_skip_file(self, path: Path, *, include_hidden: bool = False) -> bool:
        """判断搜索/列目录时是否跳过文件。"""

        if self.is_sensitive_path(path):
            return True
        return not include_hidden and path.name.startswith(".")


def _is_relative_to(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _normalize_user_path(raw_path: str) -> str:
    """规范化模型/用户传入的路径字符串。

    这里借鉴 通用 coding agent 的 read 工具思路：路径进入系统边界前先做轻量清洗。
    这不是权限判断本身，真正的授权仍然由 `resolve().relative_to(root)` 兜底。

    参数：
      raw_path: 用户或 LLM 给出的原始路径。

    返回：
      可交给 `Path` 继续解析的路径字符串。

    抛出：
      PermissionError: 命中空字节、设备路径或暂不支持的 UNC 网络路径。
    """

    text = unicodedata.normalize("NFC", str(raw_path or ".").strip())
    if "\x00" in text:
        raise PermissionError("PATH_CONTAINS_NULL_BYTE")
    if _looks_like_dangerous_device_path(text):
        raise PermissionError("DEVICE_PATH_BLOCKED")
    if os.name == "nt" and text.startswith("\\\\"):
        raise PermissionError("UNC_PATH_BLOCKED")

    # Windows 上有些模型会生成 Git Bash / WSL 风格路径：/c/Users/name/file。
    # 转成 C:\Users\name\file 后再交给 pathlib，避免跨平台语义丢失。
    if os.name == "nt":
        match = re.match(r"^/([A-Za-z])(?:/(.*))?$", text)
        if match:
            drive = match.group(1).upper()
            tail = (match.group(2) or "").replace("/", "\\")
            return f"{drive}:\\{tail}" if tail else f"{drive}:\\"
    return text


def _looks_like_dangerous_device_path(path_text: str) -> bool:
    """阻止会无限输出、阻塞或暴露进程 fd 的类 Unix 设备路径。"""

    normalized = path_text.replace("\\", "/")
    blocked_prefixes = (
        "/dev/zero",
        "/dev/random",
        "/dev/urandom",
        "/dev/stdin",
        "/proc/self/fd/",
        "/proc/0/fd/",
    )
    return any(normalized == prefix or normalized.startswith(prefix) for prefix in blocked_prefixes)
