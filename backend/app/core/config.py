from __future__ import annotations

import json
from pathlib import Path
from typing import Any
import re

from pydantic_settings import BaseSettings, SettingsConfigDict


_PROJECT_CONFIG = None
_WRITABLE_PROJECT_CONFIG_PATHS = {
    "elf.voice.mode",
    "elf.voice.default_profile_id",
    "models.agent_chat",
    "models.agent_chat.model",
    "models.agent_chat.provider",
    "models.slots.agent_chat",
    "models.slots.agent_chat.model",
    "models.slots.agent_chat.provider",
    "models.planner.model",
}


def _read_project_config() -> dict[str, Any]:
    for candidate in _config_candidates():
        if not candidate.exists():
            continue
        try:
            return json.loads(_strip_json5_syntax(candidate.read_text(encoding="utf-8")))
        except Exception:
            # 配置文件不应让导入阶段崩掉；具体问题可在启动日志/后续配置检查中再暴露。
            return {}
    return {}


def _load_project_config() -> dict[str, Any]:
    """读取仓库根目录的 config.json5。

    这里只支持 JSON5 中最常用的注释和尾逗号，避免为了一份启动配置额外引入依赖。
    环境变量仍由 pydantic-settings 读取，优先级高于这些默认值。
    """

    global _PROJECT_CONFIG
    if _PROJECT_CONFIG is not None:
        return _PROJECT_CONFIG

    _PROJECT_CONFIG = _read_project_config()
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
    return get_project_config_value(path, default)


def get_project_config_value(path: str, default: Any, *, reload: bool = False) -> Any:
    value: Any = _read_project_config() if reload else _load_project_config()
    for part in path.split("."):
        if not isinstance(value, dict) or part not in value:
            return default
        value = value[part]
    return value


def set_project_config_value(path: str, value: Any) -> None:
    """Persist a controlled project config value into config.json5.

    This writer intentionally targets project-level runtime settings instead of
    accepting arbitrary JSON patch input from users.
    """

    if path not in _WRITABLE_PROJECT_CONFIG_PATHS:
        raise ValueError(f"Project config path is not writable at runtime: {path}")

    config_path = _project_config_path_for_write()
    current_text = config_path.read_text(encoding="utf-8") if config_path.exists() else "{}\n"
    current_config = _read_project_config()
    _set_nested_config_value(current_config, path, value)
    updated_text = _patch_known_project_config_text(current_text, path, value)
    if updated_text is None:
        updated_text = json.dumps(current_config, ensure_ascii=False, indent=2) + "\n"
    config_path.write_text(updated_text, encoding="utf-8")
    global _PROJECT_CONFIG
    _PROJECT_CONFIG = current_config


def _project_config_path_for_write() -> Path:
    for candidate in _config_candidates():
        if candidate.exists():
            return candidate
    current = Path(__file__).resolve()
    return current.parents[3] / "config.json5"


def _set_nested_config_value(config: dict[str, Any], path: str, value: Any) -> None:
    target = config
    parts = path.split(".")
    for part in parts[:-1]:
        child = target.get(part)
        if not isinstance(child, dict):
            child = {}
            target[part] = child
        target = child
    target[parts[-1]] = value


def _patch_known_project_config_text(text: str, path: str, value: Any) -> str | None:
    top_key = path.split(".", 1)[0]
    if top_key not in {"elf", "models"}:
        return None
    top_span = _find_object_value_span(text, top_key, 0, len(text))
    parts = path.split(".")[1:]
    if top_span is None:
        literal = _nested_literal(parts, _json5_literal(value), indent="    ")
        return _insert_top_level_key(text, top_key, literal)
    return _patch_object_path(text, top_span[0], top_span[1], parts, _json5_literal(value))


def _json5_literal(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _insert_top_level_key(text: str, key: str, literal: str) -> str:
    root_start = text.find("{")
    root_end = text.rfind("}")
    if root_start == -1 or root_end == -1 or root_end <= root_start:
        return "{\n  " + json.dumps(key, ensure_ascii=False) + f": {literal},\n}}\n"
    body = text[root_start + 1 : root_end].rstrip()
    prefix = "" if not body.strip() or body.rstrip().endswith(",") else ","
    insertion = f'{prefix}\n  "{key}": {literal},\n'
    return text[:root_end] + insertion + text[root_end:]


def _patch_object_path(text: str, object_start: int, object_end: int, parts: list[str], literal: str) -> str:
    key = parts[0]
    if len(parts) == 1:
        return _set_direct_object_value(text, object_start, object_end, key, literal)
    child_span = _find_object_value_span(text, key, object_start, object_end)
    if child_span is None:
        nested = _nested_literal(parts[1:], literal, indent="      ")
        return _insert_direct_object_value(text, object_start, object_end, key, nested)
    return _patch_object_path(text, child_span[0], child_span[1], parts[1:], literal)


def _nested_literal(parts: list[str], literal: str, *, indent: str) -> str:
    if not parts:
        return literal
    key = json.dumps(parts[0], ensure_ascii=False)
    if len(parts) == 1:
        return "{\n" + f"{indent}{key}: {literal},\n" + indent[:-2] + "}"
    return "{\n" + f"{indent}{key}: {_nested_literal(parts[1:], literal, indent=indent + '  ')},\n" + indent[:-2] + "}"


def _set_direct_object_value(text: str, object_start: int, object_end: int, key: str, literal: str) -> str:
    value_span = _find_direct_value_span(text, key, object_start, object_end)
    if value_span is None:
        return _insert_direct_object_value(text, object_start, object_end, key, literal)
    return text[: value_span[0]] + literal + text[value_span[1] :]


def _insert_direct_object_value(text: str, object_start: int, object_end: int, key: str, literal: str) -> str:
    closing_line_start = text.rfind("\n", object_start, object_end) + 1
    closing_indent = re.match(r"\s*", text[closing_line_start:object_end]).group(0)
    child_indent = closing_indent + "  "
    body = text[object_start + 1 : object_end].rstrip()
    prefix = "" if not body.strip() or body.rstrip().endswith(",") else ","
    insertion = f'{prefix}\n{child_indent}"{key}": {literal},\n{closing_indent}'
    return text[:object_end] + insertion + text[object_end:]


def _find_object_value_span(text: str, key: str, start: int, end: int) -> tuple[int, int] | None:
    value_span = _find_direct_value_span(text, key, start, end)
    if value_span is None:
        return None
    value_start, _value_end = value_span
    if value_start >= len(text) or text[value_start] != "{":
        return None
    object_end = _find_matching_brace(text, value_start, end)
    return (value_start, object_end) if object_end is not None else None


def _find_direct_value_span(text: str, key: str, start: int, end: int) -> tuple[int, int] | None:
    pattern = re.compile(rf'"{re.escape(key)}"\s*:', re.MULTILINE)
    for match in pattern.finditer(text, start, end):
        if _is_in_json5_comment(text, start, match.start()):
            continue
        if _brace_depth(text, start, match.start()) != 1:
            continue
        value_start = _skip_ws(text, match.end(), end)
        value_end = _find_value_end(text, value_start, end)
        return value_start, value_end
    return None


def _skip_ws(text: str, index: int, end: int) -> int:
    while index < end and text[index].isspace():
        index += 1
    return index


def _find_value_end(text: str, start: int, end: int) -> int:
    in_string = False
    quote = ""
    escaped = False
    depth = 0
    index = start
    while index < end:
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            index += 1
            continue
        if char in {'"', "'"}:
            in_string = True
            quote = char
            index += 1
            continue
        if char in "{[":
            depth += 1
        elif char in "}]":
            if depth == 0:
                return index
            depth -= 1
        elif char == "," and depth == 0:
            return index
        index += 1
    return end


def _find_matching_brace(text: str, start: int, end: int) -> int | None:
    depth = 0
    in_string = False
    quote = ""
    escaped = False
    for index in range(start, end):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            continue
        if char in {'"', "'"}:
            in_string = True
            quote = char
            continue
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index
    return None


def _is_in_json5_comment(text: str, start: int, index: int) -> bool:
    in_string = False
    quote = ""
    escaped = False
    in_line_comment = False
    in_block_comment = False
    cursor = start
    while cursor < index:
        char = text[cursor]
        next_char = text[cursor + 1] if cursor + 1 < index else ""
        if in_line_comment:
            if char == "\n":
                in_line_comment = False
            cursor += 1
            continue
        if in_block_comment:
            if char == "*" and next_char == "/":
                in_block_comment = False
                cursor += 2
                continue
            cursor += 1
            continue
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            cursor += 1
            continue
        if char in {'"', "'"}:
            in_string = True
            quote = char
            cursor += 1
            continue
        if char == "/" and next_char == "/":
            in_line_comment = True
            cursor += 2
            continue
        if char == "/" and next_char == "*":
            in_block_comment = True
            cursor += 2
            continue
        cursor += 1
    return in_line_comment or in_block_comment


def _brace_depth(text: str, start: int, end: int) -> int:
    depth = 0
    in_string = False
    quote = ""
    escaped = False
    index = start
    while index < end:
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == quote:
                in_string = False
            index += 1
            continue
        next_char = text[index + 1] if index + 1 < end else ""
        if char == "/" and next_char == "/":
            newline = text.find("\n", index + 2, end)
            index = end if newline == -1 else newline + 1
            continue
        if char == "/" and next_char == "*":
            close = text.find("*/", index + 2, end)
            index = end if close == -1 else close + 2
            continue
        if char in {'"', "'"}:
            in_string = True
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
        index += 1
    return depth


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
    context_pyramid_core_memory_tokens: int = int(_config_value("context_pyramid.core_memory_tokens", 2_400))
    context_pyramid_retrieved_memory_tokens: int = int(
        _config_value("context_pyramid.retrieved_memory_tokens", 10_000)
    )
    context_pyramid_summary_tokens: int = int(_config_value("context_pyramid.summary_tokens", 4_000))
    context_pyramid_conversation_window_tokens: int = int(
        _config_value("context_pyramid.conversation_window_tokens", 10_000)
    )
    context_pyramid_recent_message_tokens: int = int(_config_value("context_pyramid.recent_message_tokens", 8_000))
    context_pyramid_adjacent_message_tokens: int = int(
        _config_value("context_pyramid.adjacent_message_tokens", 1_200)
    )
    context_pyramid_weak_retrieval_max_chunks: int = int(
        _config_value("context_pyramid.weak_retrieval_max_chunks", 5)
    )
    attachments_storage_dir: str = str(_config_value("attachments.storage_dir", "./data/uploads"))
    attachments_image_max_mb: int = int(_config_value("attachments.image_max_mb", 10))
    attachments_file_max_mb: int = int(_config_value("attachments.file_max_mb", 30))
    attachments_chat_image_default_policy: str = str(
        _config_value("attachments.chat_image_default_policy", "chat_only")
    )
    attachments_auto_extract: bool = bool(_config_value("attachments.auto_extract", True))
    attachments_vision_model: str = str(_config_value("attachments.vision_model", "qwen-vl-plus"))
    attachments_allowed_image_mime_types: list[str] = [
        str(item)
        for item in _config_value(
            "attachments.allowed_image_mime_types",
            ["image/jpeg", "image/png", "image/gif", "image/webp"],
        )
    ]
    storage_provider: str = str(_config_value("storage.provider", "local_mock"))
    storage_local_mock_dir: str = str(_config_value("storage.local_mock.dir", "./data/cloud_storage_mock"))
    storage_aliyun_region: str = str(_config_value("storage.aliyun_oss.region", "cn-hangzhou"))
    storage_aliyun_bucket: str = str(_config_value("storage.aliyun_oss.bucket", ""))
    storage_aliyun_endpoint: str = str(
        _config_value("storage.aliyun_oss.endpoint", "https://oss-cn-hangzhou.aliyuncs.com")
    )
    storage_aliyun_access_key_id_env: str = str(
        _config_value("storage.aliyun_oss.access_key_id_env", "ALIYUN_ACCESS_KEY_ID")
    )
    storage_aliyun_access_key_secret_env: str = str(
        _config_value("storage.aliyun_oss.access_key_secret_env", "ALIYUN_ACCESS_KEY_SECRET")
    )
    storage_default_storage_class: str = str(_config_value("storage.aliyun_oss.default_storage_class", "Standard"))
    storage_signed_url_ttl_seconds: int = int(_config_value("storage.aliyun_oss.signed_url_ttl_seconds", 900))
    storage_sync_enabled: bool = bool(_config_value("storage.sync.enabled", False))
    storage_sync_user_id: str = str(_config_value("storage.sync.user_id", "local-user"))
    storage_sync_pull_interval_seconds: int = int(_config_value("storage.sync.pull_interval_seconds", 900))
    storage_sync_push_interval_seconds: int = int(_config_value("storage.sync.push_interval_seconds", 900))
    storage_sync_pull_on_startup: bool = bool(_config_value("storage.sync.pull_on_startup", True))
    storage_sync_push_on_idle_seconds: int = int(_config_value("storage.sync.push_on_idle_seconds", 30))
    storage_sync_conflict_policy: str = str(_config_value("storage.sync.conflict_policy", "keep_both"))
    knowledge_image_text_extraction_mode: str = str(
        _config_value("knowledge.image_text_extraction.mode", "qwen_vl_ocr")
    )
    knowledge_image_text_extraction_provider: str = str(
        _config_value("knowledge.image_text_extraction.provider", "dashscope")
    )
    knowledge_image_text_extraction_model: str = str(
        _config_value("knowledge.image_text_extraction.model", "qwen-vl-ocr")
    )
    knowledge_image_text_extraction_max_image_bytes: int = int(
        _config_value("knowledge.image_text_extraction.max_image_bytes", 5 * 1024 * 1024)
    )
    knowledge_image_text_extraction_max_images_per_document: int = int(
        _config_value("knowledge.image_text_extraction.max_images_per_document", 80)
    )
    knowledge_image_text_extraction_min_confidence: float = float(
        _config_value("knowledge.image_text_extraction.min_confidence", 0.45)
    )
    knowledge_image_text_extraction_timeout_seconds: int = int(
        _config_value("knowledge.image_text_extraction.timeout_seconds", 60)
    )
    knowledge_image_text_extraction_max_attempts: int = int(
        _config_value("knowledge.image_text_extraction.max_attempts", 3)
    )
    knowledge_image_text_extraction_retry_backoff_seconds: float = float(
        _config_value("knowledge.image_text_extraction.retry_backoff_seconds", 0.5)
    )
    knowledge_image_ocr_languages: str = str(
        _config_value("knowledge.image_text_extraction.ocr_languages", "chi_sim+eng")
    )
    knowledge_image_ocr_timeout_seconds: int = int(
        _config_value("knowledge.image_text_extraction.ocr_timeout_seconds", 15)
    )
    voice_enabled: bool = bool(_config_value("voice.enabled", True))
    voice_asr_provider: str = str(_config_value("voice.asr_provider", "aliyun_dashscope"))
    voice_tts_provider: str = str(_config_value("voice.tts_provider", "aliyun_dashscope"))
    voice_design_provider: str = str(_config_value("voice.voice_design_provider", "aliyun_dashscope"))
    voice_max_audio_mb: int = int(_config_value("voice.max_audio_mb", 20))
    voice_language: str = str(_config_value("voice.language", "auto"))
    voice_aliyun_base_url: str = str(_config_value("voice.aliyun.base_url", "https://dashscope.aliyuncs.com"))
    voice_aliyun_asr_model: str = str(_config_value("voice.aliyun.asr_model", "qwen3-asr-flash"))
    voice_aliyun_tts_model: str = str(_config_value("voice.aliyun.tts_model", "qwen3-tts-instruct-flash"))
    voice_aliyun_voice_design_model: str = str(
        _config_value("voice.aliyun.voice_design_model", "qwen-voice-design")
    )
    voice_aliyun_voice_design_target_model: str = str(
        _config_value("voice.aliyun.voice_design_target_model", "qwen3-tts-vd-2026-01-26")
    )
    voice_aliyun_sample_rate: int = int(_config_value("voice.aliyun.sample_rate", 48_000))
    voice_aliyun_timeout_seconds: int = int(_config_value("voice.aliyun.timeout_seconds", 120))
    dashscope_api_key: str = ""
    dashscope_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    dashscope_embedding_model: str = "text-embedding-v4"
    embedding_dimensions: int = 1024
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    deepseek_api_key: str = ""
    openrouter_api_key: str = ""
    siliconflow_api_key: str = ""
    local_llm_api_key: str = ""
    anthropic_api_key: str = ""
    chat_model: str = ""
    embedding_model: str = ""
    agent_model_warmup_enabled: bool = bool(_config_value("models.warmup_on_startup", False))
    agent_model_background_warmup_enabled: bool = bool(
        _config_value("models.background_warmup_on_startup", True)
    )
    # Local Operator read-only 默认允许仓库根目录和当前用户 Home。
    # 这里可以追加更多根目录，使用分号或逗号分隔，例如：
    # LOCAL_OPERATOR_WORKSPACE_ROOTS=E:\Ai记;D:\资料;~/Documents
    local_operator_workspace_roots: str = ""
    aimemo_host: str = "127.0.0.1"
    aimemo_frontend_port: int = 5173
    aimemo_desktop_port: int = 1420
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

    @property
    def resolved_cors_origins(self) -> list[str]:
        origins = list(self.cors_origins)
        for port in {self.aimemo_frontend_port, self.aimemo_desktop_port}:
            origins.extend(
                [
                    f"http://localhost:{port}",
                    f"http://127.0.0.1:{port}",
                    f"http://{self.aimemo_host}:{port}",
                ]
            )
        return list(dict.fromkeys(origins))

    @property
    def context_pyramid_budget(self):
        from app.agent.context import ContextBudget

        return ContextBudget(
            core_memory_tokens=self.context_pyramid_core_memory_tokens,
            retrieved_memory_tokens=self.context_pyramid_retrieved_memory_tokens,
            summary_tokens=self.context_pyramid_summary_tokens,
            conversation_window_tokens=self.context_pyramid_conversation_window_tokens,
            recent_message_tokens=self.context_pyramid_recent_message_tokens,
            adjacent_message_tokens=self.context_pyramid_adjacent_message_tokens,
            weak_retrieval_max_chunks=self.context_pyramid_weak_retrieval_max_chunks,
        )


settings = Settings()
