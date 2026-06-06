__all__ = ["run_note_metadata_graph"]


def __getattr__(name: str):
    if name == "run_note_metadata_graph":
        from app.agent.graphs.note_metadata.graph import run_note_metadata_graph

        return run_note_metadata_graph
    raise AttributeError(name)
