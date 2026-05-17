from langchain_openai import ChatOpenAI

from app.core.config import settings
from app.core.timing import elapsed_ms, emit_timing, now_counter


AGENT_CHAT_MODEL = "qwen3.5-plus"
PLANNER_CHAT_MODEL = "qwen-turbo"

_agent_chat_model: ChatOpenAI | None = None
_planner_chat_model: ChatOpenAI | None = None


def get_agent_chat_model() -> ChatOpenAI:
    """创建 Ai 记默认回答模型。

    Qwen3.5 系列默认可能开启 thinking mode。普通 RAG 回答属于低延迟交互路径，
    默认关闭 thinking，避免首 token 被推理链拖慢。
    后续如果要做深度推理，可以单独提供 reasoning model 工厂。
    """

    global _agent_chat_model
    if _agent_chat_model is None:
        _agent_chat_model = _build_dashscope_chat_model(
            model=AGENT_CHAT_MODEL,
            streaming=True,
            cache_name="agent_chat",
        )
    return _agent_chat_model


def get_planner_chat_model() -> ChatOpenAI:
    """创建轻量规划模型。

    planner 负责“是否检索 + query rewrite”这类会影响整轮回答质量的判断。
    当前使用 qwen-turbo 降低 L3 worker 延迟；回答质量主要交给 generate_answer
    使用的 qwen3.5-plus 和回答提示词控制。
    """

    global _planner_chat_model
    if _planner_chat_model is None:
        _planner_chat_model = _build_dashscope_chat_model(
            model=PLANNER_CHAT_MODEL,
            streaming=False,
            cache_name="planner",
        )
    return _planner_chat_model


def warmup_agent_models() -> None:
    """服务启动时预创建模型实例。

    这里只构造本地 client，不发起真实 LLM 请求。这样可以把 ChatOpenAI/OpenAI/httpx
    的冷启动成本挪到 startup，同时避免 API Key 或网络短暂异常阻断服务启动。
    """

    if not settings.dashscope_api_key:
        emit_timing("agent.model_warmup_skipped", reason="missing_dashscope_api_key")
        return
    started_at = now_counter()
    get_planner_chat_model()
    get_agent_chat_model()
    emit_timing("agent.model_warmup_completed", total_ms=elapsed_ms(started_at))


def reset_agent_models() -> None:
    """清空模型缓存。

    主要供测试使用；未来如果支持运行时切换 API Key/provider，也会复用这个入口。
    """

    global _agent_chat_model, _planner_chat_model
    _agent_chat_model = None
    _planner_chat_model = None


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
