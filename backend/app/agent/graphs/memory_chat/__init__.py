__all__ = [
    "build_memory_chat_graph",
    "get_elf_memory_chat_graph_mermaid",
    "get_memory_chat_graph_mermaid",
    "run_memory_chat_graph",
]


def __getattr__(name: str):
    if name in {
        "build_memory_chat_graph",
        "get_elf_memory_chat_graph_mermaid",
        "get_memory_chat_graph_mermaid",
        "run_memory_chat_graph",
    }:
        from app.agent.graphs.memory_chat.graph import (
            build_memory_chat_graph,
            get_elf_memory_chat_graph_mermaid,
            get_memory_chat_graph_mermaid,
            run_memory_chat_graph,
        )

        return {
            "build_memory_chat_graph": build_memory_chat_graph,
            "get_elf_memory_chat_graph_mermaid": get_elf_memory_chat_graph_mermaid,
            "get_memory_chat_graph_mermaid": get_memory_chat_graph_mermaid,
            "run_memory_chat_graph": run_memory_chat_graph,
        }[name]
    raise AttributeError(name)
