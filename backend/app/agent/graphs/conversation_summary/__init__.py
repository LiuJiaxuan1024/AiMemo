__all__ = ["run_conversation_summary_graph"]


def __getattr__(name: str):
    if name == "run_conversation_summary_graph":
        from app.agent.graphs.conversation_summary.graph import run_conversation_summary_graph

        return run_conversation_summary_graph
    raise AttributeError(name)
