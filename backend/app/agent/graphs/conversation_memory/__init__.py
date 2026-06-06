__all__ = ["run_conversation_memory_graph"]


def __getattr__(name: str):
    if name == "run_conversation_memory_graph":
        from app.agent.graphs.conversation_memory.graph import run_conversation_memory_graph

        return run_conversation_memory_graph
    raise AttributeError(name)
