import os


def _disable_langsmith_tracing_by_default() -> None:
    """本地应用默认关闭 LangSmith tracing。

    Ai 记是 local-first 开源应用，默认不需要把 graph 执行轨迹上传到 LangSmith。
    如果用户已经显式设置 LANGSMITH_TRACING / LANGCHAIN_TRACING_V2，则尊重用户配置；
    否则使用关闭 tracing 的默认值。
    """

    enabled = os.environ.get("AIJI_ENABLE_LANGSMITH_TRACING", "").lower()
    if enabled in {"1", "true", "yes", "on"}:
        os.environ["LANGSMITH_TRACING"] = "true"
        os.environ["LANGCHAIN_TRACING_V2"] = "true"
        return
    os.environ.setdefault("LANGSMITH_TRACING", "false")
    os.environ.setdefault("LANGCHAIN_TRACING_V2", "false")
    os.environ.setdefault("LANGCHAIN_TRACING", "false")


_disable_langsmith_tracing_by_default()
