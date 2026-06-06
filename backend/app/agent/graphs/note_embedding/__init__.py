__all__ = ["run_note_embedding_graph"]


def __getattr__(name: str):
    if name == "run_note_embedding_graph":
        from app.agent.graphs.note_embedding.graph import run_note_embedding_graph

        return run_note_embedding_graph
    raise AttributeError(name)
