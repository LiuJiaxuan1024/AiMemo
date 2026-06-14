from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

from sqlmodel import Session

from app.core.config import get_project_config_value, settings
from app.services.runtime_config_service import set_persistent_runtime_config


AGENT_CHAT_MODEL = "qwen3.5-plus"
PLANNER_CHAT_MODEL = "qwen-turbo"


def reset_agent_models() -> None:
    from app.agent.model import reset_agent_models as reset_models

    reset_models()


@dataclass(frozen=True)
class ModelProviderSpec:
    provider: str
    label: str
    base_url: str
    api_key_env: str
    default_model: str
    models: tuple[str, ...]
    tool_calling: bool = True
    streaming: bool = True
    extra_body: dict[str, Any] | None = None


@dataclass(frozen=True)
class ModelAuthRef:
    source: str
    id: str | None = None


@dataclass(frozen=True)
class ResolvedModelSlot:
    slot: str
    provider: str
    model: str
    base_url: str
    api_key_env: str
    api_key: str
    temperature: float = 0.2
    streaming: bool = True
    extra_body: dict[str, Any] | None = None
    capabilities: dict[str, Any] | None = None
    auth: ModelAuthRef = ModelAuthRef(source="env", id="DASHSCOPE_API_KEY")


def agent_chat_provider_specs() -> dict[str, ModelProviderSpec]:
    return {
        "dashscope": ModelProviderSpec(
            provider="dashscope",
            label="DashScope",
            base_url=settings.dashscope_base_url,
            api_key_env="DASHSCOPE_API_KEY",
            default_model=AGENT_CHAT_MODEL,
            models=(AGENT_CHAT_MODEL, "qwen-plus", "qwen-max", "qwen-turbo"),
            extra_body={"enable_thinking": False},
        ),
        "openai": ModelProviderSpec(
            provider="openai",
            label="OpenAI",
            base_url=settings.openai_base_url,
            api_key_env="OPENAI_API_KEY",
            default_model="gpt-4.1",
            models=("gpt-4.1", "gpt-4.1-mini", "gpt-4o", "gpt-4o-mini"),
        ),
        "openai_compatible": ModelProviderSpec(
            provider="openai_compatible",
            label="OpenAI Compatible",
            base_url=settings.openai_base_url,
            api_key_env="OPENAI_API_KEY",
            default_model=AGENT_CHAT_MODEL,
            models=(AGENT_CHAT_MODEL,),
        ),
        "deepseek": ModelProviderSpec(
            provider="deepseek",
            label="DeepSeek",
            base_url="https://api.deepseek.com/v1",
            api_key_env="DEEPSEEK_API_KEY",
            default_model="deepseek-chat",
            models=("deepseek-chat", "deepseek-reasoner"),
        ),
        "openrouter": ModelProviderSpec(
            provider="openrouter",
            label="OpenRouter",
            base_url="https://openrouter.ai/api/v1",
            api_key_env="OPENROUTER_API_KEY",
            default_model="openai/gpt-4.1",
            models=("openai/gpt-4.1", "openai/gpt-4.1-mini", "anthropic/claude-sonnet-4"),
        ),
        "siliconflow": ModelProviderSpec(
            provider="siliconflow",
            label="SiliconFlow",
            base_url="https://api.siliconflow.cn/v1",
            api_key_env="SILICONFLOW_API_KEY",
            default_model="Qwen/Qwen3-235B-A22B-Instruct-2507",
            models=("Qwen/Qwen3-235B-A22B-Instruct-2507", "deepseek-ai/DeepSeek-V3"),
        ),
        "local_openai_compatible": ModelProviderSpec(
            provider="local_openai_compatible",
            label="Local OpenAI Compatible",
            base_url="http://127.0.0.1:11434/v1",
            api_key_env="LOCAL_LLM_API_KEY",
            default_model="qwen3",
            models=("qwen3", "qwen2.5", "llama3.1"),
        ),
    }


def planner_model_options() -> tuple[str, ...]:
    return (PLANNER_CHAT_MODEL, "qwen-plus", "qwen3.5-plus")


def current_agent_chat_config() -> dict[str, Any]:
    resolved = resolve_model_slot("agent_chat", reload=True)
    return resolved_model_slot_to_legacy_config(resolved)


def current_agent_chat_provider() -> str:
    return str(current_agent_chat_config().get("provider") or "dashscope").strip().lower()


def current_agent_chat_model() -> str:
    return str(current_agent_chat_config().get("model") or AGENT_CHAT_MODEL).strip()


def current_planner_model() -> str:
    model = str(get_project_config_value("models.planner.model", PLANNER_CHAT_MODEL, reload=True) or "").strip()
    return model or PLANNER_CHAT_MODEL


def set_agent_chat_provider(session: Session, provider: str):
    normalized = provider.strip().lower()
    specs = agent_chat_provider_specs()
    spec = specs.get(normalized)
    if spec is None:
        return None, f"不支持的 agent.chat.provider：{provider}。"
    if not spec.tool_calling:
        return None, f"agent.chat.provider={normalized} 不支持 tool calling，不能作为 ReAct 主模型。"
    api_key = _api_key_for_env(spec.api_key_env)
    if not api_key:
        return None, f"切换到 {normalized} 前需要先配置 {spec.api_key_env}。"

    old_config = current_agent_chat_config()
    next_slot = _agent_chat_slot_for_provider(spec)
    current_model = str(old_config.get("model") or "").strip()
    if current_model and current_model in spec.models:
        next_slot["model"] = current_model

    set_persistent_runtime_config(session, "models.slots.agent_chat", next_slot)
    reset_agent_models()
    return resolved_model_slot_to_legacy_config(_resolve_slot_config("agent_chat", next_slot, {})), None


def set_agent_chat_model(session: Session, model: str):
    normalized_model = normalize_model_name(model)
    if normalized_model is None:
        return None, "agent.chat.model 不能为空，只能包含模型名称字符。"
    config = current_agent_chat_config()
    provider = str(config.get("provider") or "dashscope").strip().lower()
    spec = agent_chat_provider_specs().get(provider)
    if spec is None:
        return None, f"当前 agent.chat.provider={provider} 不受支持，无法设置模型。"
    if not _api_key_for_env(spec.api_key_env):
        return None, f"设置 {provider} 模型前需要先配置 {spec.api_key_env}。"
    slot_config = {
        "provider": provider,
        "model": normalized_model,
        "temperature": float(config.get("temperature", 0.2)),
        "streaming": bool(config.get("streaming", spec.streaming)),
    }
    set_persistent_runtime_config(session, "models.slots.agent_chat", slot_config)
    reset_agent_models()
    return resolved_model_slot_to_legacy_config(_resolve_slot_config("agent_chat", slot_config, {})), None


def set_planner_model(session: Session, model: str):
    normalized_model = normalize_model_name(model)
    if normalized_model is None:
        return None, "planner.model 不能为空，只能包含模型名称字符。"
    if not _api_key_for_env("DASHSCOPE_API_KEY"):
        return None, "设置 planner.model 前需要先配置 DASHSCOPE_API_KEY。"
    set_persistent_runtime_config(session, "models.planner.model", normalized_model)
    reset_agent_models()
    return normalized_model, None


def normalize_model_name(value: str) -> str | None:
    normalized = value.strip().strip('"').strip("'").strip()
    if not normalized:
        return None
    if len(normalized) > 160:
        return None
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,159}", normalized):
        return None
    return normalized


ConfigReader = Callable[[str, Any], Any]


def resolve_model_slot(
    slot: str,
    *,
    reload: bool = False,
    config_reader: ConfigReader | None = None,
) -> ResolvedModelSlot:
    if slot != "agent_chat":
        raise ValueError(f"Unsupported model slot: {slot}")

    reader = config_reader or (lambda path, default: get_project_config_value(path, default, reload=reload))
    raw_slots = reader("models.slots", None)
    raw_providers = reader("models.providers", None)
    if isinstance(raw_slots, dict) and isinstance(raw_slots.get(slot), dict):
        return _resolve_slot_config(slot, raw_slots[slot], raw_providers if isinstance(raw_providers, dict) else {})

    raw_legacy = reader("models.agent_chat", None)
    if isinstance(raw_legacy, dict):
        return _resolve_legacy_configured_slot(slot, raw_legacy)

    return _resolve_legacy_configured_slot(
        slot,
        {
            "provider": "dashscope",
            "model": settings.chat_model or AGENT_CHAT_MODEL,
            "base_url": settings.dashscope_base_url,
            "api_key_env": "DASHSCOPE_API_KEY",
            "temperature": 0.2,
            "streaming": True,
            "capabilities": {"tool_calling": True, "json_mode": True, "vision": False},
            "extra_body": {"enable_thinking": False},
        },
    )


def resolved_model_slot_to_legacy_config(resolved: ResolvedModelSlot) -> dict[str, Any]:
    config: dict[str, Any] = {
        "provider": resolved.provider,
        "model": resolved.model,
        "base_url": resolved.base_url,
        "api_key_env": resolved.api_key_env,
        "temperature": resolved.temperature,
        "streaming": resolved.streaming,
        "capabilities": dict(resolved.capabilities or {}),
    }
    if resolved.extra_body:
        config["extra_body"] = dict(resolved.extra_body)
    return config


def _resolve_slot_config(slot: str, raw_slot: dict[str, Any], raw_providers: dict[str, Any]) -> ResolvedModelSlot:
    provider = str(raw_slot.get("provider") or "dashscope").strip().lower()
    raw_provider = raw_providers.get(provider)
    if not isinstance(raw_provider, dict):
        spec = agent_chat_provider_specs().get(provider)
        raw_provider = _provider_config_from_spec(spec) if spec is not None else {}

    base_url = str(raw_provider.get("base_url") or raw_provider.get("baseUrl") or _default_base_url_for_provider(provider)).strip()
    auth_ref = _parse_auth_ref(raw_provider.get("api_key"), provider=provider, base_url=base_url)
    api_key_env = auth_ref.id or _default_api_key_env_for_provider(provider)
    provider_extra_body = raw_provider.get("extra_body")
    slot_extra_body = raw_slot.get("extra_body")
    extra_body = _merge_dicts(provider_extra_body, slot_extra_body)
    if extra_body is None and provider == "dashscope":
        extra_body = {"enable_thinking": False}

    capabilities = _merge_dicts(raw_provider.get("capabilities"), raw_slot.get("capabilities")) or {}
    return ResolvedModelSlot(
        slot=slot,
        provider=provider,
        model=str(raw_slot.get("model") or raw_provider.get("default_model") or raw_provider.get("defaultModel") or AGENT_CHAT_MODEL).strip(),
        base_url=base_url,
        api_key_env=api_key_env,
        api_key=_resolve_api_key(auth_ref),
        temperature=float(raw_slot.get("temperature", raw_provider.get("temperature", 0.2))),
        streaming=bool(raw_slot.get("streaming", raw_provider.get("streaming", True))),
        extra_body=extra_body,
        capabilities=capabilities,
        auth=auth_ref,
    )


def _resolve_legacy_configured_slot(slot: str, raw_config: dict[str, Any]) -> ResolvedModelSlot:
    provider = str(raw_config.get("provider") or "dashscope").strip().lower()
    base_url = str(raw_config.get("base_url") or _default_base_url_for_provider(provider)).strip()
    api_key_env = str(raw_config.get("api_key_env") or _default_api_key_env_for_provider(provider)).strip()
    extra_body = raw_config.get("extra_body")
    if extra_body is None and provider == "dashscope":
        extra_body = {"enable_thinking": False}
    capabilities = raw_config.get("capabilities")
    return ResolvedModelSlot(
        slot=slot,
        provider=provider,
        model=str(raw_config.get("model") or AGENT_CHAT_MODEL).strip(),
        base_url=base_url,
        api_key_env=api_key_env,
        api_key=_api_key_for_env(api_key_env),
        temperature=float(raw_config.get("temperature", 0.2)),
        streaming=bool(raw_config.get("streaming", True)),
        extra_body=extra_body if isinstance(extra_body, dict) else {},
        capabilities=capabilities if isinstance(capabilities, dict) else {},
        auth=ModelAuthRef(source="env", id=api_key_env),
    )


def _provider_config_from_spec(spec: ModelProviderSpec | None) -> dict[str, Any]:
    if spec is None:
        return {}
    config: dict[str, Any] = {
        "label": spec.label,
        "base_url": spec.base_url,
        "api_key": {"source": "env", "id": spec.api_key_env},
        "default_model": spec.default_model,
        "models": list(spec.models),
        "streaming": spec.streaming,
        "capabilities": {"tool_calling": spec.tool_calling, "json_mode": True, "vision": False},
    }
    if spec.extra_body is not None:
        config["extra_body"] = spec.extra_body
    return config


def _parse_auth_ref(raw_auth: Any, *, provider: str, base_url: str) -> ModelAuthRef:
    if isinstance(raw_auth, dict):
        source = str(raw_auth.get("source") or "env").strip().lower()
        auth_id = raw_auth.get("id")
        return ModelAuthRef(source=source, id=str(auth_id).strip() if auth_id else None)
    if isinstance(raw_auth, str) and raw_auth.strip():
        return ModelAuthRef(source="env", id=raw_auth.strip())
    if provider == "local_openai_compatible" or _is_local_base_url(base_url):
        return ModelAuthRef(source="none")
    return ModelAuthRef(source="env", id=_default_api_key_env_for_provider(provider))


def _resolve_api_key(auth_ref: ModelAuthRef) -> str:
    if auth_ref.source == "none":
        return "no-api-key-required"
    if auth_ref.source != "env":
        return ""
    return _api_key_for_env(auth_ref.id or "")


def _merge_dicts(base: Any, override: Any) -> dict[str, Any] | None:
    merged: dict[str, Any] = {}
    if isinstance(base, dict):
        merged.update(base)
    if isinstance(override, dict):
        merged.update(override)
    return merged or None


def _is_local_base_url(base_url: str) -> bool:
    parsed = urlparse(base_url)
    host = (parsed.hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"} or host.startswith("192.168.") or host.startswith("10.")


def _agent_chat_config_for_provider(spec: ModelProviderSpec) -> dict[str, Any]:
    config: dict[str, Any] = {
        "provider": spec.provider,
        "model": spec.default_model,
        "base_url": spec.base_url,
        "api_key_env": spec.api_key_env,
        "temperature": 0.2,
        "streaming": spec.streaming,
        "capabilities": {
            "tool_calling": spec.tool_calling,
            "json_mode": True,
            "vision": False,
        },
    }
    if spec.extra_body is not None:
        config["extra_body"] = spec.extra_body
    return config


def _agent_chat_slot_for_provider(spec: ModelProviderSpec) -> dict[str, Any]:
    return {
        "provider": spec.provider,
        "model": spec.default_model,
        "temperature": 0.2,
        "streaming": spec.streaming,
    }


def _api_key_for_env(env_name: str) -> str:
    value = os.getenv(env_name)
    if value:
        return value
    settings_attr = env_name.lower()
    if hasattr(settings, settings_attr):
        return str(getattr(settings, settings_attr) or "")
    return ""


def _default_base_url_for_provider(provider: str) -> str:
    spec = agent_chat_provider_specs().get(provider)
    if spec is not None:
        return spec.base_url
    return settings.openai_base_url


def _default_api_key_env_for_provider(provider: str) -> str:
    spec = agent_chat_provider_specs().get(provider)
    if spec is not None:
        return spec.api_key_env
    return "OPENAI_API_KEY"
