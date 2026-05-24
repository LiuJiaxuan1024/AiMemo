from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic_settings import BaseSettings, SettingsConfigDict


_PROJECT_CONFIG = None


def _load_project_config() -> dict[str, Any]:
    """读取仓库根目录的 config.json5。

    这里只支持 JSON5 中最常用的注释和尾逗号，避免为了一份启动配置额外引入依赖。
    环境变量仍由 pydantic-settings 读取，优先级高于这些默认值。
    """

    global _PROJECT_CONFIG
    if _PROJECT_CONFIG is not None:
        return _PROJECT_CONFIG

    for candidate in _config_candidates():
        if not candidate.exists():
            continue
        try:
            _PROJECT_CONFIG = json.loads(_strip_json5_syntax(candidate.read_text(encoding="utf-8")))
            return _PROJECT_CONFIG
        except Exception:
            # 配置文件不应让导入阶段崩掉；具体问题可在启动日志/后续配置检查中再暴露。
            _PROJECT_CONFIG = {}
            return _PROJECT_CONFIG
    _PROJECT_CONFIG = {}
    return _PROJECT_CONFIG


def _config_candidates() -> list[Path]:
    current = Path(__file__).resolve()
    repo_root = current.parents[3]
    return [
        repo_root / "config.json5",
        repo_root / "backend" / "config.json5",
        Path.cwd() / "config.json5",
    ]


def _config_value(path: str, default: Any) -> Any:
    value: Any = _load_project_config()
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return default
        value = value[part]
    return value


def _strip_json5_syntax(text: str) -> str:
    """移除 JSON5 注释和尾逗号，保留字符串内容不变。"""

    without_comments: list[str] = []
    in_string = False
    quote = ""
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        next_char = text[index + 1] if index + 1 < len(text) else ""
        if in_string:
            without_comments.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            index += 1
            continue
        if char in {"'", '"'}:
            # JSON 本身不支持单引号；配置模板使用双引号。这里保留单引号，
            # 让 json.loads 给出失败结果，而不是悄悄改语义。
            in_string = True
            quote = char
            without_comments.append(char)
            index += 1
            continue
        if char == "/" and next_char == "/":
            index = text.find("\n", index)
            if index == -1:
                break
            without_comments.append("\n")
            index += 1
            continue
        if char == "/" and next_char == "*":
            end = text.find("*/", index + 2)
            index = len(text) if end == -1 else end + 2
            continue
        without_comments.append(char)
        index += 1
    compact = "".join(without_comments)
    import re

    return re.sub(r",\s*([}\]])", r"\1", compact)


class Settings(BaseSettings):
    app_env: str = "development"
    database_url: str = "sqlite:///./data/ai_note.db"
    langgraph_checkpoint_path: str = "./data/langgraph_checkpoints.db"
    job_worker_enabled: bool = True
    job_worker_poll_interval_seconds: float = 2.0
    job_running_timeout_seconds: int = 600
    job_reconciler_enabled: bool = True
    job_reconciler_interval_seconds: float = 30.0
    # 启动时是否清理所有已终止的后台命令任务（exited / failed / killed / orphaned / unknown）。
    # 子进程是 detached 的，结束后不会自动从 DB 里消失；开启此项可让重启自动收尾旧任务的日志和记录。
    background_task_cleanup_on_startup: bool = True
    local_operator_exec_default_timeout_ms: int = int(
        _config_value("local_operator.exec_command.default_timeout_ms", 180_000)
    )
    local_operator_exec_max_timeout_ms: int = int(
        _config_value("local_operator.exec_command.max_timeout_ms", 600_000)
    )
    local_operator_exec_default_max_output_bytes: int = int(
        _config_value("local_operator.exec_command.default_max_output_bytes", 64 * 1024)
    )
    local_operator_exec_max_output_bytes: int = int(
        _config_value("local_operator.exec_command.max_output_bytes", 256 * 1024)
    )
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_embedding_model: str = "text-embedding-v4"
    embedding_dimensions: int = 1024
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    chat_model: str = ""
    embedding_model: str = ""
    # Local Operator read-only 默认允许仓库根目录和当前用户 Home。
    # 这里可以追加更多根目录，使用分号或逗号分隔，例如：
    # LOCAL_OPERATOR_WORKSPACE_ROOTS=E:\Ai记;D:\资料;~/Documents
    local_operator_workspace_roots: str = ""
    cors_origins: list[str] = [
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:1420",
        "http://127.0.0.1:1420",
        "tauri://localhost",
    ]

    model_config = SettingsConfigDict(
        # 支持两种常见启动方式：
        # 1. 在仓库根目录执行脚本，此时读取根目录 `.env`；
        # 2. 进入 backend/ 后启动 uvicorn，此时额外读取 `../.env`。
        # 后面的文件优先级更高，方便用户在 backend/.env 做本地临时覆盖。
        env_file=(".env", "../.env", "backend/.env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )


settings = Settings()
