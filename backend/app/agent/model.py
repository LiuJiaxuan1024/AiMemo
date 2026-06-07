from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from typing import Any

from langchain_core.tools import BaseTool
from langchain_openai import ChatOpenAI

from app.core.config import get_project_config_value, settings
from app.core.timing import elapsed_ms, emit_timing, now_counter


AGENT_CHAT_MODEL = "qwen3.5-plus"
PLANNER_CHAT_MODEL = "qwen-turbo"
AGENT_CHAT_SLOT = "agent_chat"

_planner_chat_model: ChatOpenAI | None = None
_vision_chat_model: ChatOpenAI | None = None
_chat_model_cache: dict[str, ChatOpenAI] = {}
_model_cache_lock = threading.RLock()


@dataclass(frozen=True)
class ChatModelConfig:
    slot: str
    provider: str
    model: str
    base_url: str
    api_key_env: str
    api_key: str
    temperature: float = 0.2
    streaming: bool = False
    extra_body: dict[str, Any] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)

    @property
    def cache_key(self) -> str:
        payload = {
            "slot": self.slot,
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "temperature": self.temperature,
            "streaming": self.streaming,
            "extra_body": self.extra_body,
        }
        return json.dumps(payload, ensure_ascii=True, sort_keys=True)


def get_agent_chat_model() -> ChatOpenAI:
    """创建 Ai 记主聊天/通用文本生成模型。

    Qwen3.5 系列默认可能开启 thinking mode。普通 RAG 回答属于低延迟交互路径，
    默认关闭 thinking，避免首 token 被推理链拖慢。
    后续如果要做深度推理，可以单独提供 reasoning model 工厂。
    """

    return get_chat_model(AGENT_CHAT_SLOT)


def get_chat_model(slot: str) -> ChatOpenAI:
    """按模型槽位创建并缓存 ChatOpenAI 实例。

    第一阶段只开放 agent_chat slot；planner/vision 仍走固定 DashScope 工厂。
    """

    if slot != AGENT_CHAT_SLOT:
        raise ValueError(f"Unsupported chat model slot: {slot}")

    config = _resolve_agent_chat_config()
    _validate_agent_chat_config(config)
    with _model_cache_lock:
        if config.cache_key not in _chat_model_cache:
            _chat_model_cache[config.cache_key] = _build_openai_compatible_chat_model(
                config=config,
                cache_name=slot,
            )
        return _chat_model_cache[config.cache_key]


def get_agent_chat_model_with_tools(tools: list[BaseTool]):
    """返回绑定本地工具 schema 的主回答模型。

    ReAct 版 memory_chat_graph 不再用规则 classifier 预判是否调用工具，
    而是把工具 schema 交给模型，由模型通过 OpenAI-compatible tool_calls
    自行决定下一步。
    """

    return get_agent_chat_model().bind_tools(tools, tool_choice="auto")


def get_planner_chat_model() -> ChatOpenAI:
    """创建轻量规划模型。

    planner 用于少量轻量 JSON 判断和可选 query rewrite。
    个人笔记 L3 默认先走 cheap recall，不再用 planner 决定是否执行轻量召回。
    回答质量主要交给 qwen3.5-plus 和回答提示词控制。
    """

    global _planner_chat_model
    with _model_cache_lock:
        if _planner_chat_model is None:
            _planner_chat_model = _build_dashscope_chat_model(
                model=PLANNER_CHAT_MODEL,
                streaming=False,
                cache_name="planner",
            )
        return _planner_chat_model


def get_vision_chat_model() -> ChatOpenAI:
    """创建图片/附件解析模型。

    视觉解析独立于主 ReAct 模型，避免把普通工具循环绑定到多模态模型上。
    """

    global _vision_chat_model
    with _model_cache_lock:
        if _vision_chat_model is None:
            _vision_chat_model = _build_dashscope_chat_model(
                model=settings.attachments_vision_model,
                streaming=False,
                cache_name="vision",
            )
        return _vision_chat_model


def warmup_agent_models() -> None:
    """服务启动时预创建模型实例。

    这里只构造本地 client，不发起真实 LLM 请求。这样可以把 ChatOpenAI/OpenAI/httpx
    的冷启动成本挪到 startup，同时避免 API Key 或网络短暂异常阻断服务启动。
    """

    agent_config = _resolve_agent_chat_config()
    if not agent_config.api_key:
        emit_timing(
            "agent.model_warmup_skipped",
            reason="missing_agent_chat_api_key",
            api_key_env=agent_config.api_key_env,
            provider=agent_config.provider,
            model=agent_config.model,
        )
        return
    started_at = now_counter()
    try:
        get_agent_chat_model()
        if settings.dashscope_api_key:
            get_planner_chat_model()
            get_vision_chat_model()
        else:
            emit_timing("agent.model_warmup_partial", reason="missing_dashscope_api_key_for_aux_models")
    except Exception as exc:
        reset_agent_models()
        emit_timing(
            "agent.model_warmup_failed",
            total_ms=elapsed_ms(started_at),
            error_type=type(exc).__name__,
            error=str(exc),
        )
        return
    emit_timing("agent.model_warmup_completed", total_ms=elapsed_ms(started_at))


def reset_agent_models() -> None:
    """清空模型缓存。

    主要供测试使用；未来如果支持运行时切换 API Key/provider，也会复用这个入口。
    """

    global _planner_chat_model, _vision_chat_model
    with _model_cache_lock:
        _chat_model_cache.clear()
        _planner_chat_model = None
        _vision_chat_model = None


def _resolve_agent_chat_config() -> ChatModelConfig:
    raw_config = get_project_config_value("models.agent_chat", None)
    if isinstance(raw_config, dict):
        return _resolve_configured_chat_model(raw_config)
    return _resolve_legacy_agent_chat_config()


def _resolve_legacy_agent_chat_config() -> ChatModelConfig:
    return ChatModelConfig(
        slot=AGENT_CHAT_SLOT,
        provider="dashscope",
        model=settings.chat_model or AGENT_CHAT_MODEL,
        base_url=settings.dashscope_base_url,
        api_key_env="DASHSCOPE_API_KEY",
        api_key=settings.dashscope_api_key,
        temperature=0.2,
        streaming=True,
        extra_body={"enable_thinking": False},
        capabilities={
            "tool_calling": True,
            "json_mode": True,
            "streaming": True,
            "vision": False,
        },
    )


def _resolve_configured_chat_model(raw_config: dict[str, Any]) -> ChatModelConfig:
    provider = str(raw_config.get("provider") or "dashscope").strip().lower()
    default_base_url = _default_base_url_for_provider(provider)
    api_key_env = str(raw_config.get("api_key_env") or _default_api_key_env_for_provider(provider)).strip()
    model = str(raw_config.get("model") or AGENT_CHAT_MODEL).strip()
    base_url = str(raw_config.get("base_url") or default_base_url).strip()
    extra_body = raw_config.get("extra_body")
    if extra_body is None and provider == "dashscope":
        extra_body = {"enable_thinking": False}
    capabilities = raw_config.get("capabilities")

    return ChatModelConfig(
        slot=AGENT_CHAT_SLOT,
        provider=provider,
        model=model,
        base_url=base_url,
        api_key_env=api_key_env,
        api_key=_api_key_for_env(api_key_env),
        temperature=float(raw_config.get("temperature", 0.2)),
        streaming=bool(raw_config.get("streaming", True)),
        extra_body=extra_body if isinstance(extra_body, dict) else {},
        capabilities=capabilities if isinstance(capabilities, dict) else {},
    )


def _default_base_url_for_provider(provider: str) -> str:
    if provider == "dashscope":
        return settings.dashscope_base_url
    if provider == "openai":
        return settings.openai_base_url
    if provider == "deepseek":
        return "https://api.deepseek.com/v1"
    if provider == "openrouter":
        return "https://openrouter.ai/api/v1"
    if provider == "siliconflow":
        return "https://api.siliconflow.cn/v1"
    if provider == "local_openai_compatible":
        return "http://127.0.0.1:11434/v1"
    return settings.openai_base_url


def _default_api_key_env_for_provider(provider: str) -> str:
    provider_envs = {
        "dashscope": "DASHSCOPE_API_KEY",
        "openai": "OPENAI_API_KEY",
        "deepseek": "DEEPSEEK_API_KEY",
        "openrouter": "OPENROUTER_API_KEY",
        "siliconflow": "SILICONFLOW_API_KEY",
        "local_openai_compatible": "LOCAL_LLM_API_KEY",
        "anthropic": "ANTHROPIC_API_KEY",
    }
    return provider_envs.get(provider, "OPENAI_API_KEY")


def _api_key_for_env(env_name: str) -> str:
    value = os.getenv(env_name)
    if value:
        return value
    settings_attr = env_name.lower()
    if hasattr(settings, settings_attr):
        return str(getattr(settings, settings_attr) or "")
    return ""


def _validate_agent_chat_config(config: ChatModelConfig) -> None:
    if not config.api_key:
        raise RuntimeError(f"{config.api_key_env} is required to initialize the agent_chat model.")
    if not config.model:
        raise RuntimeError("models.agent_chat.model is required.")
    if not config.base_url:
        raise RuntimeError("models.agent_chat.base_url is required.")
    if config.provider == "anthropic":
        raise RuntimeError("provider=anthropic is documented for future use but is not implemented yet.")
    if config.provider not in {
        "dashscope",
        "openai",
        "openai_compatible",
        "deepseek",
        "openrouter",
        "siliconflow",
        "local_openai_compatible",
    }:
        raise RuntimeError(f"Unsupported agent_chat provider: {config.provider}")
    if config.capabilities.get("tool_calling") is False:
        raise RuntimeError("models.agent_chat.capabilities.tool_calling must be true for the ReAct agent.")


def _build_openai_compatible_chat_model(*, config: ChatModelConfig, cache_name: str) -> ChatOpenAI:
    """创建 OpenAI-compatible ChatOpenAI 实例。"""

    total_started_at = now_counter()
    validate_started_at = now_counter()
    _validate_agent_chat_config(config)
    validate_ms = elapsed_ms(validate_started_at)

    kwargs_started_at = now_counter()
    kwargs: dict[str, Any] = {
        "api_key": config.api_key,
        "base_url": config.base_url,
        "model": config.model,
        "temperature": config.temperature,
        "streaming": config.streaming,
    }
    if config.extra_body:
        kwargs["extra_body"] = config.extra_body
    kwargs_ms = elapsed_ms(kwargs_started_at)

    constructor_started_at = now_counter()
    chat_model = ChatOpenAI(**kwargs)
    constructor_ms = elapsed_ms(constructor_started_at)
    emit_timing(
        "agent.model_factory_timing",
        cache_name=cache_name,
        provider=config.provider,
        model=config.model,
        streaming=config.streaming,
        total_ms=elapsed_ms(total_started_at),
        validate_ms=validate_ms,
        kwargs_ms=kwargs_ms,
        constructor_ms=constructor_ms,
    )
    return chat_model


def _build_dashscope_chat_model(*, model: str, streaming: bool, cache_name: str) -> ChatOpenAI:
    """创建 DashScope OpenAI-compatible ChatOpenAI 实例。"""

    total_started_at = now_counter()
    validate_started_at = now_counter()
    if not settings.dashscope_api_key:
        raise RuntimeError("DASHSCOPE_API_KEY is required to initialize the agent model.")
    validate_ms = elapsed_ms(validate_started_at)

    kwargs_started_at = now_counter()
    kwargs = {
        "api_key": settings.dashscope_api_key,
        "base_url": settings.dashscope_base_url,
        "model": model,
        "temperature": 0.2,
        # LangGraph 的 stream_mode="messages" 依赖底层 chat model 产生 token 事件。
        # invoke 仍会聚合完整结果返回，所以非流式接口不受影响。
        "streaming": streaming,
        "extra_body": {"enable_thinking": False},
    }
    kwargs_ms = elapsed_ms(kwargs_started_at)

    constructor_started_at = now_counter()
    chat_model = ChatOpenAI(**kwargs)
    constructor_ms = elapsed_ms(constructor_started_at)
    emit_timing(
        "agent.model_factory_timing",
        cache_name=cache_name,
        model=model,
        streaming=streaming,
        total_ms=elapsed_ms(total_started_at),
        validate_ms=validate_ms,
        kwargs_ms=kwargs_ms,
        constructor_ms=constructor_ms,
    )
    return chat_model
