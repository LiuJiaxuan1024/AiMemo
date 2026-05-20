from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path

from langgraph.graph import END, START, StateGraph
from sqlmodel import Session

from app.agent.checkpoints import get_sqlite_checkpointer
from app.agent.graphs.memory_chat.nodes import (
    AnswerGenerator,
    ElfBubbleAnswerGenerator,
    NoteRetriever,
    RetrievalPlanner,
    build_l0_current_input_node,
    build_l1_recent_messages_node,
    build_l2_summary_node,
    build_l3_retrieved_memory_node,
    build_l4_core_memory_node,
    build_generate_elf_bubble_answer_node,
    build_generate_answer_node,
    build_load_turn_state_node,
    build_local_operator_context_node,
    build_merge_prompt_context_node,
    build_persist_messages_node,
    dispatch_context_workers,
    route_answer_mode,
)
from app.agent.graphs.memory_chat.state import MemoryChatGraphState
from app.agent.streaming import map_langgraph_stream_chunk
from app.core.database import session_scope
from app.models.chat_message import ChatMessage


SessionFactory = Callable[[], AbstractContextManager[Session]]

CONTEXT_WORKER_NODES = [
    "build_l4_core_memory",
    "build_l3_retrieved_memory",
    "build_l2_summary",
    "build_l1_recent_messages",
    "build_l0_current_input",
    "build_local_operator_context",
]


def build_memory_chat_graph(
    *,
    session_factory: SessionFactory,
    planner: RetrievalPlanner | None = None,
    retriever: NoteRetriever | None = None,
    answer_generator: AnswerGenerator | None = None,
    bubble_answer_generator: ElfBubbleAnswerGenerator | None = None,
):
    """构建记忆对话 graph。

    依赖全部显式注入，便于测试替换 fake classifier/retriever/answer_generator。
    """

    selected_retriever = retriever or None
    graph = StateGraph(MemoryChatGraphState)
    graph.add_node("load_turn_state", build_load_turn_state_node(session_factory))
    graph.add_node("dispatch_context_workers", lambda state: {})
    graph.add_node("build_l4_core_memory", build_l4_core_memory_node(session_factory))
    graph.add_node(
        "build_l3_retrieved_memory",
        build_l3_retrieved_memory_node(
            session_factory,
            planner=planner,
            retriever=selected_retriever,
        )
        if selected_retriever
        else build_l3_retrieved_memory_node(session_factory, planner=planner),
    )
    graph.add_node("build_l2_summary", build_l2_summary_node())
    graph.add_node("build_l1_recent_messages", build_l1_recent_messages_node())
    graph.add_node("build_l0_current_input", build_l0_current_input_node())
    graph.add_node("build_local_operator_context", build_local_operator_context_node(session_factory))
    graph.add_node("merge_prompt_context", build_merge_prompt_context_node())
    graph.add_node("generate_answer", build_generate_answer_node(answer_generator))
    graph.add_node(
        "generate_elf_bubble_answer",
        build_generate_elf_bubble_answer_node(bubble_answer_generator),
    )
    graph.add_node("persist_messages", build_persist_messages_node(session_factory))

    graph.add_edge(START, "load_turn_state")
    graph.add_edge("load_turn_state", "dispatch_context_workers")
    # Send worker 模式运行时可以动态分发多个节点；这里显式列出目标节点，
    # 让 LangGraph 的 Mermaid 绘图器能画出真实的并行扇出结构。
    graph.add_conditional_edges(
        "dispatch_context_workers",
        dispatch_context_workers,
        CONTEXT_WORKER_NODES,
    )
    graph.add_edge("build_l4_core_memory", "merge_prompt_context")
    graph.add_edge("build_l3_retrieved_memory", "merge_prompt_context")
    graph.add_edge("build_l2_summary", "merge_prompt_context")
    graph.add_edge("build_l1_recent_messages", "merge_prompt_context")
    graph.add_edge("build_l0_current_input", "merge_prompt_context")
    graph.add_edge("build_local_operator_context", "merge_prompt_context")
    graph.add_conditional_edges(
        "merge_prompt_context",
        route_answer_mode,
        ["generate_answer", "generate_elf_bubble_answer"],
    )
    graph.add_edge("generate_answer", "persist_messages")
    graph.add_edge("generate_elf_bubble_answer", "persist_messages")
    graph.add_edge("persist_messages", END)
    return graph


def run_memory_chat_graph(
    *,
    conversation_id: int,
    user_message: str,
    session_factory: SessionFactory,
    checkpoint_path: str,
    planner: RetrievalPlanner | None = None,
    retriever: NoteRetriever | None = None,
    answer_generator: AnswerGenerator | None = None,
    bubble_answer_generator: ElfBubbleAnswerGenerator | None = None,
    interrupt_after: list[str] | None = None,
    user_message_id: int | None = None,
    assistant_message_id: int | None = None,
    answer_mode: str = "text",
) -> MemoryChatGraphState:
    """执行一轮记忆对话。

    thread_id 固定为 conversation:{conversation_id}。同一会话的多轮 graph 执行会共享
    LangGraph checkpoint 历史，但每轮输入会覆盖 user_message 等派生字段。
    """

    checkpoint_file = Path(checkpoint_path)
    checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
    thread_id = f"conversation:{conversation_id}"

    with get_sqlite_checkpointer(str(checkpoint_file)) as checkpointer:
        app = build_memory_chat_graph(
            session_factory=session_factory,
            planner=planner,
            retriever=retriever,
            answer_generator=answer_generator,
            bubble_answer_generator=bubble_answer_generator,
        ).compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = app.get_state(config)
        graph_input = (
            None
            if snapshot.next
            else {
                "conversation_id": conversation_id,
                "user_message": user_message,
                "answer_mode": answer_mode,
                "user_message_id": user_message_id or 0,
                "assistant_message_id": assistant_message_id or 0,
            }
        )
        result = app.invoke(
            graph_input,
            config,
            interrupt_after=interrupt_after,
        )
        snapshot = app.get_state(config)
        checkpoint_id = snapshot.config["configurable"].get("checkpoint_id")
        _write_checkpoint_id_to_messages(
            session_factory,
            user_message_id=result.get("user_message_id"),
            assistant_message_id=result.get("assistant_message_id"),
            checkpoint_id=checkpoint_id,
        )
        result["graph_checkpoint_id"] = checkpoint_id
        return result


def stream_memory_chat_graph(
    *,
    conversation_id: int,
    user_message: str,
    session_factory: SessionFactory,
    checkpoint_path: str,
    planner: RetrievalPlanner | None = None,
    retriever: NoteRetriever | None = None,
    answer_generator: AnswerGenerator | None = None,
    bubble_answer_generator: ElfBubbleAnswerGenerator | None = None,
    user_message_id: int | None = None,
    assistant_message_id: int | None = None,
    answer_mode: str = "text",
):
    """以 LangGraph 原生流执行一轮记忆对话。

    同时开启 updates 和 messages：
      - updates 用于观察节点执行进度。
      - messages 用于捕获 LLM token。

    这里不会把 LangGraph 原始 chunk 直接暴露给 service，而是先映射为 Ai 记内部事件。
    这样前端协议可以保持稳定，后续接 custom/debug/events 时也更好扩展。
    """

    checkpoint_file = Path(checkpoint_path)
    checkpoint_file.parent.mkdir(parents=True, exist_ok=True)
    thread_id = f"conversation:{conversation_id}"

    with get_sqlite_checkpointer(str(checkpoint_file)) as checkpointer:
        app = build_memory_chat_graph(
            session_factory=session_factory,
            planner=planner,
            retriever=retriever,
            answer_generator=answer_generator,
            bubble_answer_generator=bubble_answer_generator,
        ).compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": thread_id}}
        snapshot = app.get_state(config)
        graph_input = (
            None
            if snapshot.next
            else {
                "conversation_id": conversation_id,
                "user_message": user_message,
                "answer_mode": answer_mode,
                "user_message_id": user_message_id or 0,
                "assistant_message_id": assistant_message_id or 0,
            }
        )
        latest_state: MemoryChatGraphState = {}
        for stream_item in app.stream(
            graph_input,
            config,
            stream_mode=["updates", "messages"],
        ):
            if not isinstance(stream_item, tuple) or len(stream_item) != 2:
                continue
            mode, chunk = stream_item
            for event in map_langgraph_stream_chunk(str(mode), chunk):
                if event["event"] == "node":
                    latest_state.update(event["state_update"])
                    yield {
                        "event": "node",
                        "node": event["node"],
                        "state": dict(latest_state),
                    }
                elif event["event"] == "answer_delta":
                    yield {
                        "event": "answer_delta",
                        "node": event["node"],
                        "content": event["content"],
                        "metadata": event["metadata"],
                    }
                elif event["event"] == "bubble_delta":
                    yield {
                        "event": "bubble_delta",
                        "node": event["node"],
                        "content": event["content"],
                        "metadata": event["metadata"],
                    }
                elif event["event"] == "internal_token":
                    yield {
                        "event": "internal_token",
                        "node": event["node"],
                        "content": event["content"],
                        "metadata": event["metadata"],
                    }

        snapshot = app.get_state(config)
        final_state = dict(snapshot.values)
        checkpoint_id = snapshot.config["configurable"].get("checkpoint_id")
        _write_checkpoint_id_to_messages(
            session_factory,
            user_message_id=final_state.get("user_message_id"),
            assistant_message_id=final_state.get("assistant_message_id"),
            checkpoint_id=checkpoint_id,
        )
        final_state["graph_checkpoint_id"] = checkpoint_id
        yield {
            "event": "done",
            "node": "",
            "state": final_state,
        }


def get_memory_chat_graph_mermaid() -> str:
    """返回 Memory Chat Graph 的 LangGraph Mermaid 图。"""

    graph = build_memory_chat_graph(session_factory=session_scope)
    app = graph.compile()
    return app.get_graph(xray=True).draw_mermaid()


def _write_checkpoint_id_to_messages(
    session_factory: SessionFactory,
    *,
    user_message_id: int | None,
    assistant_message_id: int | None,
    checkpoint_id: str | None,
) -> None:
    """graph 完成后，把最终 checkpoint_id 回写到业务消息。

    checkpoint_id 只有在节点完成并由 LangGraph 写快照后才能可靠获得，所以这里放在
    invoke 之后处理，而不是 persist_messages 节点内部处理。
    """

    if not checkpoint_id:
        return
    message_ids = [message_id for message_id in [user_message_id, assistant_message_id] if message_id]
    if not message_ids:
        return
    with session_factory() as session:
        for message_id in message_ids:
            message = session.get(ChatMessage, message_id)
            if message:
                message.checkpoint_id = checkpoint_id
                session.add(message)
        session.commit()
