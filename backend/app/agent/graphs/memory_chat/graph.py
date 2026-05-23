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
    build_current_conversation_window_node,
    build_l0_current_input_node,
    build_l1_recent_messages_node,
    build_l2_summary_node,
    build_l3_retrieved_memory_node,
    build_l4_core_memory_node,
    build_generate_elf_bubble_answer_node,
    build_generate_answer_node,
    build_agent_think_node,
    build_check_tool_policy_node,
    build_load_turn_state_node,
    build_merge_prompt_context_node,
    build_plan_task_node,
    build_persist_messages_node,
    build_observe_tool_result_node,
    build_run_read_tool_node,
    build_run_exec_tool_node,
    build_run_write_tool_node,
    build_select_tool_node,
    build_verify_goal_node,
    dispatch_context_workers,
    route_after_agent_think,
    route_after_tool_policy,
    route_after_verify_goal,
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
    "build_current_conversation_window",
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
    graph.add_node("build_current_conversation_window", build_current_conversation_window_node())
    graph.add_node("merge_prompt_context", build_merge_prompt_context_node())
    graph.add_node("plan_task", build_plan_task_node())
    graph.add_node("agent_think", build_agent_think_node())
    graph.add_node("select_tool", build_select_tool_node())
    graph.add_node("check_tool_policy", build_check_tool_policy_node())
    graph.add_node("run_read_tool", build_run_read_tool_node(session_factory))
    graph.add_node("run_write_tool", build_run_write_tool_node(session_factory))
    graph.add_node("run_exec_tool", build_run_exec_tool_node(session_factory))
    graph.add_node("observe_tool_result", build_observe_tool_result_node())
    graph.add_node("verify_goal", build_verify_goal_node())
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
    graph.add_edge("build_current_conversation_window", "merge_prompt_context")
    graph.add_edge("merge_prompt_context", "plan_task")
    graph.add_edge("plan_task", "agent_think")
    graph.add_conditional_edges(
        "agent_think",
        route_after_agent_think,
        ["select_tool", "verify_goal", "generate_answer", "generate_elf_bubble_answer"],
    )
    graph.add_edge("select_tool", "check_tool_policy")
    graph.add_conditional_edges(
        "check_tool_policy",
        route_after_tool_policy,
        ["run_read_tool", "run_write_tool", "run_exec_tool", "observe_tool_result"],
    )
    graph.add_edge("run_read_tool", "observe_tool_result")
    graph.add_edge("run_write_tool", "observe_tool_result")
    graph.add_edge("run_exec_tool", "observe_tool_result")
    graph.add_edge("observe_tool_result", "agent_think")
    graph.add_conditional_edges(
        "verify_goal",
        route_after_verify_goal,
        ["agent_think", "generate_answer", "generate_elf_bubble_answer"],
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
        graph_input = _resolve_graph_input_for_turn(
            app,
            config,
            snapshot=snapshot,
            conversation_id=conversation_id,
            user_message=user_message,
            answer_mode=answer_mode,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
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
        graph_input = _resolve_graph_input_for_turn(
            app,
            config,
            snapshot=snapshot,
            conversation_id=conversation_id,
            user_message=user_message,
            answer_mode=answer_mode,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
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
                elif event["event"] == "thought_snapshot":
                    yield {
                        "event": "thought_snapshot",
                        "node": event["node"],
                        "thoughts": event["thoughts"],
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


def _resolve_graph_input_for_turn(
    app,
    config: dict,
    *,
    snapshot,
    conversation_id: int,
    user_message: str,
    answer_mode: str,
    user_message_id: int | None,
    assistant_message_id: int | None,
) -> MemoryChatGraphState | None:
    """判断本次调用是恢复旧 graph，还是新用户输入要开启新一轮。

    LangGraph 在 `snapshot.next` 非空时会从旧节点继续；即使传入新的 input，
    它也会把新字段合并进旧 checkpoint 后继续跑旧节点。对聊天来说，这会造成
    “上一轮 pending tool action 吞掉下一轮用户输入”的严重串状态。

    因此只有当本次请求仍然指向同一条用户/assistant 草稿消息时才允许恢复；
    如果来了新的消息，就先把旧 checkpoint 过期关闭，再从 START 重新进入。
    """

    next_input: MemoryChatGraphState = {
        "conversation_id": conversation_id,
        "user_message": user_message,
        "answer_mode": answer_mode,  # type: ignore[typeddict-item]
        "user_message_id": user_message_id or 0,
        "assistant_message_id": assistant_message_id or 0,
    }
    if not snapshot.next:
        return next_input
    if _is_same_turn_resume(
        snapshot.values,
        user_message=user_message,
        user_message_id=user_message_id,
        assistant_message_id=assistant_message_id,
    ):
        return None
    _expire_stale_checkpoint(app, config, snapshot=snapshot, next_input=next_input)
    return next_input


def _is_same_turn_resume(
    values: dict,
    *,
    user_message: str,
    user_message_id: int | None,
    assistant_message_id: int | None,
) -> bool:
    """同一轮恢复判断。

    有 message_id 时以业务消息 ID 为准；没有 message_id 的测试/非流式路径再退回
    到 user_message 文本判断。这样既支持真正 resume，也避免新输入误接旧现场。
    """

    current_user_id = int(values.get("user_message_id") or 0)
    current_assistant_id = int(values.get("assistant_message_id") or 0)
    if user_message_id or assistant_message_id:
        return (
            current_user_id == int(user_message_id or 0)
            and current_assistant_id == int(assistant_message_id or 0)
        )
    return str(values.get("user_message") or "") == user_message


def _expire_stale_checkpoint(
    app,
    config: dict,
    *,
    snapshot,
    next_input: MemoryChatGraphState,
) -> None:
    """把旧中断现场标记为过期，并关闭 `snapshot.next`。

    `as_node="persist_messages"` 会让 LangGraph 把该 checkpoint 视作已到达终点。
    我们同时清空工具队列和 pending action，后续新输入从 START 进入时不会继承旧动作。
    """

    old_task = dict((snapshot.values or {}).get("task") or {})
    expired_task = _mark_task_superseded(old_task) if old_task else {}
    app.update_state(
        config,
        {
            "planned_tool_actions": [],
            "pending_tool_action": None,
            "tool_policy_result": {},
            "tool_observations": [],
            "tool_observation_context": "",
            "agent_decision": {"type": "final_answer", "reason": "旧 checkpoint 被新用户输入过期。"},
            "task": {},
            "expired_task": expired_task,
            "world_state": {},
            "world_status": {},
            "task_boundary": {
                "type": "expired_stale_checkpoint",
                "reason": "检测到新用户输入到达时旧 checkpoint 仍停在中间节点，已关闭旧现场并开启新一轮。",
                "previous_task_id": old_task.get("id"),
                "active_task_id": None,
                "expired_task_id": old_task.get("id"),
            },
            "conversation_id": next_input.get("conversation_id"),
            "user_message": next_input.get("user_message"),
            "answer_mode": next_input.get("answer_mode"),
            "user_message_id": next_input.get("user_message_id"),
            "assistant_message_id": next_input.get("assistant_message_id"),
        },
        as_node="persist_messages",
    )


def _mark_task_superseded(task: dict) -> dict:
    """返回一个标记为 SUPERSEDED 的旧 task 副本，供 checkpoint/debug 查看。"""

    if not task:
        return {}
    updated = dict(task)
    updated["status"] = "SUPERSEDED"
    history = list(updated.get("execution_history") or [])
    history.append(
        {
            "type": "superseded",
            "summary": "旧 task 被新的用户输入取代。",
            "payload": {},
        }
    )
    updated["execution_history"] = history
    return updated


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
