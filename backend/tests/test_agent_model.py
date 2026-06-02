import pytest

from app.agent.model import (
    AGENT_CHAT_MODEL,
    PLANNER_CHAT_MODEL,
    get_agent_chat_model,
    get_planner_chat_model,
    get_vision_chat_model,
    reset_agent_models,
    warmup_agent_models,
)
from app.core.config import settings


def test_agent_chat_model_disables_qwen_thinking_by_default(monkeypatch):
    """默认回答模型关闭 Qwen thinking，避免普通回答首 token 被思考链拖慢。"""

    reset_agent_models()
    monkeypatch.setattr("app.agent.model.get_project_config_value", lambda path, default: default)
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")

    model = get_agent_chat_model()

    assert model.model_name == AGENT_CHAT_MODEL
    assert model.streaming is True
    assert model.extra_body == {"enable_thinking": False}


def test_agent_chat_model_uses_configured_openai_provider(monkeypatch):
    reset_agent_models()
    monkeypatch.setattr(
        "app.agent.model.get_project_config_value",
        lambda path, default: {
            "provider": "openai",
            "model": "gpt-test",
            "base_url": "https://openai.test/v1",
            "api_key_env": "OPENAI_API_KEY",
            "temperature": 0.3,
            "streaming": False,
            "capabilities": {"tool_calling": True},
        }
        if path == "models.agent_chat"
        else default,
    )
    monkeypatch.setenv("OPENAI_API_KEY", "openai-test-key")

    model = get_agent_chat_model()

    assert model.model_name == "gpt-test"
    assert str(model.openai_api_base) == "https://openai.test/v1"
    assert model.openai_api_key.get_secret_value() == "openai-test-key"
    assert model.temperature == 0.3
    assert model.streaming is False
    assert model.extra_body is None


def test_agent_chat_model_uses_configured_openai_compatible_provider(monkeypatch):
    reset_agent_models()
    monkeypatch.setattr(
        "app.agent.model.get_project_config_value",
        lambda path, default: {
            "provider": "openai_compatible",
            "model": "deepseek-chat",
            "base_url": "https://api.deepseek.com/v1",
            "api_key_env": "DEEPSEEK_API_KEY",
            "capabilities": {"tool_calling": True},
        }
        if path == "models.agent_chat"
        else default,
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "deepseek-test-key")

    model = get_agent_chat_model()

    assert model.model_name == "deepseek-chat"
    assert str(model.openai_api_base) == "https://api.deepseek.com/v1"
    assert model.openai_api_key.get_secret_value() == "deepseek-test-key"
    assert model.streaming is True


def test_agent_chat_model_rejects_tool_calling_disabled(monkeypatch):
    reset_agent_models()
    monkeypatch.setattr(
        "app.agent.model.get_project_config_value",
        lambda path, default: {
            "provider": "openai",
            "model": "gpt-test",
            "api_key_env": "OPENAI_API_KEY",
            "capabilities": {"tool_calling": False},
        }
        if path == "models.agent_chat"
        else default,
    )
    monkeypatch.setattr(settings, "openai_api_key", "openai-test-key")

    with pytest.raises(RuntimeError, match="tool_calling"):
        get_agent_chat_model()


def test_planner_chat_model_uses_fast_qwen_turbo(monkeypatch):
    """planner 使用轻量模型降低 L3 小 JSON 判断延迟。"""

    reset_agent_models()
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")

    model = get_planner_chat_model()

    assert model.model_name == PLANNER_CHAT_MODEL
    assert model.streaming is False
    assert model.extra_body == {"enable_thinking": False}


def test_vision_chat_model_uses_attachment_vision_model(monkeypatch):
    reset_agent_models()
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")
    monkeypatch.setattr(settings, "attachments_vision_model", "qwen-vl-test")

    model = get_vision_chat_model()

    assert model.model_name == "qwen-vl-test"
    assert model.streaming is False
    assert model.extra_body == {"enable_thinking": False}


def test_agent_models_are_cached(monkeypatch):
    """同一进程内复用 ChatOpenAI 实例，避免每轮对话重复冷启动 client。"""

    reset_agent_models()
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")

    assert get_agent_chat_model() is get_agent_chat_model()
    assert get_planner_chat_model() is get_planner_chat_model()


def test_warmup_agent_models_creates_cached_instances(monkeypatch):
    """startup warmup 只创建实例，不发起 LLM 请求。"""

    reset_agent_models()
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")

    warmup_agent_models()

    assert get_agent_chat_model().model_name == AGENT_CHAT_MODEL
    assert get_planner_chat_model().model_name == PLANNER_CHAT_MODEL


def test_warmup_agent_models_does_not_raise_on_client_init_failure(monkeypatch):
    """模型 client 预热失败不应阻断 FastAPI startup。"""

    reset_agent_models()
    monkeypatch.setattr(settings, "dashscope_api_key", "test-key")
    monkeypatch.setattr(
        "app.agent.model.get_planner_chat_model",
        lambda: (_ for _ in ()).throw(ValueError("bad proxy")),
    )

    warmup_agent_models()
