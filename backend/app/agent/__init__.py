__all__ = [
    "AGENT_CHAT_MODEL",
    "PLANNER_CHAT_MODEL",
    "get_agent_chat_model",
    "get_chat_model",
    "get_planner_chat_model",
    "reset_agent_models",
    "warmup_agent_models",
]


def __getattr__(name: str):
    if name in __all__:
        from app.agent import model

        return getattr(model, name)
    raise AttributeError(name)
