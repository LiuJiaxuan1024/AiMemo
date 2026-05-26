import json
from datetime import timedelta

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.agent.checkpoints import get_sqlite_checkpointer
from app.agent.graphs.memory_chat.graph import build_memory_chat_graph
from app.agent.graphs.memory_chat.graph import get_memory_chat_graph_mermaid
from app.core.config import settings
from app.core.database import session_scope
from app.models.agent_operation import AgentOperation
from app.models.chat_message import ChatMessage
from app.models.chat_turn import ChatTurn
from app.models.note import utc_now
from app.schemas.chat import (
    ChatActiveTurnListRead,
    ChatActiveTurnRead,
    ChatCheckpointStateRead,
    ChatTurnGraphRead,
    ChatTurnStateHistoryRead,
)
from app.schemas.conversation import ChatMessageRead
from app.schemas.search import NoteSearchResult


MEMORY_CHAT_NODE_ORDER = [
    "load_turn_state",
    "dispatch_context_workers",
    "build_l4_core_memory",
    "build_l3_retrieved_memory",
    "build_l2_summary",
    "build_l1_recent_messages",
    "build_l0_current_input",
    "build_current_conversation_window",
    "merge_prompt_context",
    "plan_task",
    "agent",
    "tools",
    "observe_tool_result",
    "verify_goal",
    "generate_elf_bubble_answer",
    "persist_messages",
]

STALE_CHAT_TURN_TIMEOUT_SECONDS = 10 * 60
TOOLS_NOT_ENTERED_ERROR_CODE = "TOOLS_NODE_NOT_ENTERED_AFTER_AGENT_TOOL_CALL"


def initial_node_statuses() -> dict[str, str]:
    """创建一轮 Memory Chat Graph 的初始节点状态表。"""

    return {node_name: "pending" for node_name in MEMORY_CHAT_NODE_ORDER}


def create_running_chat_turn(
    session: Session,
    *,
    conversation_id: int,
    thread_id: str,
) -> ChatTurn:
    """创建 running 状态的 turn 记录。

    参数：
      session: 当前数据库会话。
      conversation_id: 业务会话 ID。
      thread_id: LangGraph thread_id，固定为 conversation:{conversation_id}。
    """

    turn = ChatTurn(
        conversation_id=conversation_id,
        thread_id=thread_id,
        node_statuses=json.dumps(initial_node_statuses(), ensure_ascii=False),
    )
    session.add(turn)
    session.commit()
    session.refresh(turn)
    return turn


def attach_chat_turn_messages(
    session: Session,
    turn_id: int,
    *,
    user_message_id: int,
    assistant_message_id: int,
) -> None:
    """把预创建的业务消息绑定到 running turn。

    参数：
      session: 当前数据库会话。
      turn_id: 本轮 ChatTurn ID。
      user_message_id: 本轮用户消息 ID。
      assistant_message_id: 本轮 assistant 草稿消息 ID。
    """

    turn = session.get(ChatTurn, turn_id)
    if turn is None:
        return
    turn.user_message_id = user_message_id
    turn.assistant_message_id = assistant_message_id
    turn.updated_at = utc_now()
    session.add(turn)
    session.commit()


def update_chat_turn_progress(
    session: Session,
    turn_id: int,
    *,
    node_statuses: dict[str, str],
    debug_payload: dict | None = None,
) -> None:
    """更新 turn 的节点状态，用于前端实时显示 graph 进度。

    参数：
      session: 当前数据库会话。
      turn_id: 本轮 ChatTurn ID。
      node_statuses: 节点状态表。
      debug_payload: 可选性能埋点，会随节点状态一起写入，供 graph 面板查看。
    """

    turn = session.get(ChatTurn, turn_id)
    if turn is None:
        return
    turn.node_statuses = json.dumps(node_statuses, ensure_ascii=False)
    if debug_payload is not None:
        turn.debug_payload = json.dumps(debug_payload, ensure_ascii=False)
    turn.updated_at = utc_now()
    session.add(turn)
    session.commit()


def interrupt_chat_turn(
    session: Session,
    turn_id: int,
    *,
    checkpoint_id: str | None,
    node_statuses: dict[str, str],
    pending_interrupt: dict,
    debug_payload: dict | None = None,
) -> None:
    """把 turn 标记为等待用户选择。"""

    turn = session.get(ChatTurn, turn_id)
    if turn is None:
        return
    turn.status = "interrupted"
    turn.checkpoint_id = checkpoint_id
    turn.node_statuses = json.dumps(node_statuses, ensure_ascii=False)
    payload = debug_payload if debug_payload is not None else _decode_json_any(turn.debug_payload, fallback={})
    payload.setdefault("interrupts", [])
    payload["pending_interrupt"] = pending_interrupt
    turn.debug_payload = json.dumps(payload, ensure_ascii=False)
    turn.updated_at = utc_now()
    session.add(turn)
    session.commit()


def mark_chat_turn_resuming(
    session: Session,
    turn_id: int,
    *,
    node_statuses: dict[str, str],
    debug_payload: dict | None = None,
) -> None:
    """用户已经提交选择，turn 回到 running。"""

    turn = session.get(ChatTurn, turn_id)
    if turn is None:
        return
    turn.status = "running"
    turn.node_statuses = json.dumps(node_statuses, ensure_ascii=False)
    payload = debug_payload if debug_payload is not None else _decode_json_any(turn.debug_payload, fallback={})
    if isinstance(payload, dict):
        payload.pop("pending_interrupt", None)
        turn.debug_payload = json.dumps(payload, ensure_ascii=False)
    turn.updated_at = utc_now()
    session.add(turn)
    session.commit()


def complete_chat_turn(
    session: Session,
    turn_id: int,
    *,
    user_message_id: int,
    assistant_message_id: int,
    checkpoint_id: str | None,
    node_statuses: dict[str, str],
    context_layers: list[dict],
    retrieved_chunks: list[dict],
    debug_payload: dict | None = None,
) -> ChatTurn:
    """把 turn 标记为完成，并保存排查所需的上下文与检索证据。"""

    turn = _get_turn_or_404(session, turn_id)
    turn.user_message_id = user_message_id
    turn.assistant_message_id = assistant_message_id
    turn.checkpoint_id = checkpoint_id
    turn.status = "completed"
    turn.node_statuses = json.dumps(node_statuses, ensure_ascii=False)
    turn.context_layers = json.dumps(context_layers, ensure_ascii=False)
    turn.retrieved_chunks = json.dumps(retrieved_chunks, ensure_ascii=False)
    if debug_payload is not None:
        turn.debug_payload = json.dumps(debug_payload, ensure_ascii=False)
    turn.error = ""
    turn.updated_at = utc_now()
    session.add(turn)
    session.commit()
    session.refresh(turn)
    return turn


def fail_chat_turn(
    session: Session,
    turn_id: int,
    *,
    node_statuses: dict[str, str],
    error: str,
    debug_payload: dict | None = None,
) -> None:
    """把 turn 标记为失败，保留已经执行到的节点状态。"""

    turn = session.get(ChatTurn, turn_id)
    if turn is None:
        return
    turn.status = "failed"
    turn.node_statuses = json.dumps(node_statuses, ensure_ascii=False)
    if debug_payload is not None:
        turn.debug_payload = json.dumps(debug_payload, ensure_ascii=False)
    turn.error = error
    turn.updated_at = utc_now()
    session.add(turn)
    session.commit()


def list_active_chat_turns(
    session: Session,
    *,
    conversation_id: int,
) -> ChatActiveTurnListRead:
    """列出指定会话里所有 status=running/interrupted 的 turn。

    用户在 assistant 还在生成时切走另一个会话、再点回来时，前端需要知道
    "这条会话现在有一个正在跑的 turn"。chat_turn_buffer 还保留着事件流，
    前端拿到 turn_id 后就能订阅 /turns/{turn_id}/events/stream 把后续增量接回来。

    DB 里 running 状态是事实来源；buffer 是事件流缓存（可能已经过 retention 回收）。
    返回 running turns 时附带 user/assistant 消息和最近一份 node_statuses，
    便于前端先恢复消息列表 + 状态条，再决定要不要订阅 SSE。
    """

    recover_stale_chat_turns(session, conversation_id=conversation_id)
    turns = session.exec(
        select(ChatTurn)
        .where(
            ChatTurn.conversation_id == conversation_id,
            ChatTurn.status.in_(["running", "interrupted"]),
        )
        .order_by(ChatTurn.created_at, ChatTurn.id)
    ).all()
    items: list[ChatActiveTurnRead] = []
    for turn in turns:
        items.append(_to_chat_active_turn_read(session, turn))
    return ChatActiveTurnListRead(items=items)


def _to_chat_active_turn_read(session: Session, turn: ChatTurn) -> ChatActiveTurnRead:
    user = _load_message(session, turn.user_message_id)
    assistant = _load_message(session, turn.assistant_message_id)
    pending_interrupt = _pending_interrupt_from_turn(turn) if turn.status == "interrupted" else None
    return ChatActiveTurnRead(
        turn_id=turn.id or 0,
        conversation_id=turn.conversation_id,
        status=turn.status,
        node_statuses=_decode_json_object(turn.node_statuses),
        pending_interrupt=pending_interrupt,
        user_message=_message_to_read(user, turn.id) if user else None,
        assistant_message=_message_to_read(assistant, turn.id, pending_interrupt=pending_interrupt) if assistant else None,
        started_at=turn.created_at,
        updated_at=turn.updated_at,
    )


def _load_message(session: Session, message_id: int | None) -> ChatMessage | None:
    if message_id is None:
        return None
    return session.get(ChatMessage, message_id)


def _message_to_read(
    message: ChatMessage,
    turn_id: int | None,
    *,
    pending_interrupt: dict | None = None,
) -> ChatMessageRead:
    return ChatMessageRead(
        id=message.id or 0,
        conversation_id=message.conversation_id,
        role=message.role,
        content=message.content,
        parent_id=message.parent_id,
        checkpoint_id=message.checkpoint_id,
        status=message.status,
        token_count=message.token_count,
        turn_id=turn_id,
        pending_interrupt=pending_interrupt,
        created_at=message.created_at,
        updated_at=message.updated_at,
    )


def get_chat_turn_graph_by_message(
    session: Session,
    *,
    conversation_id: int,
    assistant_message_id: int,
) -> ChatTurnGraphRead:
    """通过 assistant 消息反查本轮 graph 调试视图。"""

    turn = session.exec(
        select(ChatTurn).where(
            ChatTurn.conversation_id == conversation_id,
            ChatTurn.assistant_message_id == assistant_message_id,
        )
    ).first()
    if turn is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat turn graph was not found for this message",
        )
    recover_stale_chat_turns(session, conversation_id=conversation_id, turn_id=turn.id)
    session.refresh(turn)
    return _to_chat_turn_graph_read(turn)


def get_chat_turn_graph_by_turn(
    session: Session,
    *,
    conversation_id: int,
    turn_id: int,
) -> ChatTurnGraphRead:
    """通过 turn_id 读取 graph 调试视图。

    运行中的 assistant 消息还没有最终完成，但 turn 在 SSE 开始时就已经创建。
    前端可以用该接口在生成过程中查看 graph 状态。
    """

    turn = session.exec(
        select(ChatTurn).where(
            ChatTurn.id == turn_id,
            ChatTurn.conversation_id == conversation_id,
        )
    ).first()
    if turn is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat turn graph was not found",
        )
    recover_stale_chat_turns(session, conversation_id=conversation_id, turn_id=turn.id)
    session.refresh(turn)
    return _to_chat_turn_graph_read(turn)


def recover_stale_chat_turns(
    session: Session,
    *,
    conversation_id: int | None = None,
    turn_id: int | None = None,
    timeout_seconds: int = STALE_CHAT_TURN_TIMEOUT_SECONDS,
) -> int:
    """把陈旧 running turn 收敛为 failed，并写入可排查的诊断 payload。

    SSE/桌面精灵请求被浏览器刷新、进程关闭或异常打断时，worker 可能来不及走
    chat_service 的 except/finally 路径，DB 中就会留下 status=running 的 turn。
    这里在读取 active turns / graph 时做懒恢复，避免用户长期看到“还在运行”。
    """

    cutoff = utc_now() - timedelta(seconds=timeout_seconds)
    statement = select(ChatTurn).where(ChatTurn.status == "running", ChatTurn.updated_at < cutoff)
    if conversation_id is not None:
        statement = statement.where(ChatTurn.conversation_id == conversation_id)
    if turn_id is not None:
        statement = statement.where(ChatTurn.id == turn_id)

    recovered = 0
    for turn in session.exec(statement).all():
        diagnostic = _diagnose_stale_chat_turn(session, turn)
        node_statuses = _decode_json_object(turn.node_statuses)
        for node_name, node_status in list(node_statuses.items()):
            if node_status == "running":
                node_statuses[node_name] = "failed"
        # 如果 agent 已经产出 tool_calls，但 tools 从未执行，直接把 tools 节点标为 failed，
        # graph 面板会比单纯 pending 更清楚地暴露断点位置。
        if diagnostic.get("code") == TOOLS_NOT_ENTERED_ERROR_CODE:
            node_statuses["tools"] = "failed"

        debug_payload = _decode_json_any(turn.debug_payload, fallback={})
        debug_payload.setdefault("diagnostics", []).append(diagnostic)
        debug_payload.setdefault("events", {})["stale_turn_recovered"] = 0
        turn.status = "failed"
        turn.node_statuses = json.dumps(node_statuses, ensure_ascii=False)
        turn.debug_payload = json.dumps(debug_payload, ensure_ascii=False)
        turn.error = diagnostic["message"]
        turn.updated_at = utc_now()
        session.add(turn)

        if turn.assistant_message_id:
            assistant = session.get(ChatMessage, turn.assistant_message_id)
            if assistant and assistant.status == "streaming":
                assistant.status = "failed"
                assistant.updated_at = utc_now()
                session.add(assistant)
        recovered += 1

    if recovered:
        session.commit()
    return recovered


def _diagnose_stale_chat_turn(session: Session, turn: ChatTurn) -> dict:
    """识别常见卡点，优先暴露 agent 已请求工具但 tools 未落审计的断点。"""

    node_statuses = _decode_json_object(turn.node_statuses)
    debug_payload = _decode_json_any(turn.debug_payload, fallback={})
    agent_state = (
        debug_payload.get("nodes", {})
        .get("agent", {})
        .get("state", {})
        if isinstance(debug_payload, dict)
        else {}
    )
    tool_calls = []
    if isinstance(agent_state, dict):
        decision = agent_state.get("agent_decision") or {}
        if isinstance(decision, dict):
            tool_calls = [call for call in decision.get("tool_calls") or [] if isinstance(call, dict)]

    operations = session.exec(
        select(AgentOperation).where(
            AgentOperation.conversation_id == turn.conversation_id,
            AgentOperation.created_at >= turn.created_at,
            AgentOperation.created_at <= turn.updated_at,
        )
    ).all()
    recent_operation_count = len(operations)

    if (
        node_statuses.get("agent") == "succeeded"
        and node_statuses.get("tools") == "pending"
        and tool_calls
        and recent_operation_count == 0
    ):
        tool_names = [
            str(call.get("name") or call.get("tool_name") or "unknown")
            for call in tool_calls[:8]
        ]
        return {
            "code": TOOLS_NOT_ENTERED_ERROR_CODE,
            "message": (
                "agent 已生成工具调用，但 tools 节点没有落地任何 AgentOperation 审计记录；"
                "通常表示 SSE/桌面请求在 agent->tools 之间中断，或 LangGraph 未继续调度 tools。"
            ),
            "turn_id": turn.id,
            "conversation_id": turn.conversation_id,
            "tool_call_count": len(tool_calls),
            "tool_names": tool_names,
            "operation_count_since_turn": recent_operation_count,
            "updated_at": turn.updated_at.isoformat(),
        }

    return {
        "code": "STALE_RUNNING_CHAT_TURN",
        "message": "该对话轮次长时间保持 running，已自动标记为 failed 以便排查。",
        "turn_id": turn.id,
        "conversation_id": turn.conversation_id,
        "node_statuses": node_statuses,
        "operation_count_since_turn": recent_operation_count,
        "updated_at": turn.updated_at.isoformat(),
    }


def get_chat_turn_state_history(
    session: Session,
    *,
    conversation_id: int,
    turn_id: int,
    checkpoint_path: str | None = None,
    limit: int = 40,
) -> ChatTurnStateHistoryRead:
    """读取本轮所在 LangGraph thread 的原生 checkpoint state history。

    参数：
      session: 当前数据库会话，用来校验 turn 和 conversation 的业务归属。
      conversation_id: 业务会话 ID。
      turn_id: ChatTurn ID。
      checkpoint_path: LangGraph SQLite checkpoint 文件路径，测试可替换。
      limit: 最多返回多少个 checkpoint 快照。LangGraph 默认按 checkpoint_id 倒序返回。

    返回：
      checkpoint 时间线。每一帧都是 LangGraph `StateSnapshot` 的压缩 JSON 版本。
    """

    turn = session.exec(
        select(ChatTurn).where(
            ChatTurn.id == turn_id,
            ChatTurn.conversation_id == conversation_id,
        )
    ).first()
    if turn is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Chat turn graph was not found",
        )

    with get_sqlite_checkpointer(checkpoint_path or settings.langgraph_checkpoint_path) as checkpointer:
        app = build_memory_chat_graph(session_factory=session_scope).compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": turn.thread_id}}
        snapshots = list(app.get_state_history(config, limit=limit))

    return ChatTurnStateHistoryRead(
        turn_id=turn.id or 0,
        conversation_id=turn.conversation_id,
        thread_id=turn.thread_id,
        checkpoint_id=turn.checkpoint_id,
        states=[_to_checkpoint_state_read(snapshot) for snapshot in snapshots],
    )


def _to_chat_turn_graph_read(turn: ChatTurn) -> ChatTurnGraphRead:
    node_statuses = _decode_json_object(turn.node_statuses)
    debug_payload = _decode_json_any(turn.debug_payload, fallback={})
    mermaid = _highlight_memory_chat_mermaid(get_memory_chat_graph_mermaid(), node_statuses)
    return ChatTurnGraphRead(
        turn_id=turn.id or 0,
        conversation_id=turn.conversation_id,
        user_message_id=turn.user_message_id,
        assistant_message_id=turn.assistant_message_id,
        thread_id=turn.thread_id,
        checkpoint_id=turn.checkpoint_id,
        status=turn.status,
        node_statuses=node_statuses,
        mermaid=mermaid,
        # 工具循环已经迁入 Memory Chat 主图，当前 read/write 节点直接在主图中染色。
        # 后续如果某个工具节点再次拆成真正 LangGraph 子图，再把对应 mermaid 挂到这里。
        subgraphs={},
        context_layers=_decode_json_list(turn.context_layers),
        retrieved_chunks=[
            NoteSearchResult(**chunk) for chunk in _decode_json_list(turn.retrieved_chunks)
        ],
        debug_payload=debug_payload,
        error=turn.error,
    )


def _to_checkpoint_state_read(snapshot) -> ChatCheckpointStateRead:
    """把 LangGraph StateSnapshot 转成 API schema。"""

    config = snapshot.config or {}
    parent_config = snapshot.parent_config or {}
    configurable = config.get("configurable") or {}
    parent_configurable = parent_config.get("configurable") or {}
    return ChatCheckpointStateRead(
        checkpoint_id=configurable.get("checkpoint_id"),
        parent_checkpoint_id=parent_configurable.get("checkpoint_id"),
        created_at=snapshot.created_at,
        next=list(snapshot.next or []),
        tasks=[_compact_task(task) for task in snapshot.tasks or []],
        interrupts=[_compact_interrupt(interrupt) for interrupt in snapshot.interrupts or []],
        metadata=_compact_debug_object(snapshot.metadata) if snapshot.metadata is not None else None,
        values=_compact_debug_value(snapshot.values) if isinstance(snapshot.values, dict) else {},
    )


def _compact_task(task) -> dict:
    """压缩 LangGraph PregelTask，保留调试所需字段。"""

    return {
        "id": getattr(task, "id", ""),
        "name": getattr(task, "name", ""),
        "path": _compact_debug_value(getattr(task, "path", ())),
        "error": str(getattr(task, "error", "") or ""),
        "interrupts": _compact_debug_value(getattr(task, "interrupts", ())),
        "result": _compact_debug_value(getattr(task, "result", None)),
    }


def _compact_interrupt(interrupt) -> dict:
    """把 LangGraph Interrupt 对象压成前端可比较的普通 dict。

    LangGraph 的 `StateSnapshot.interrupts` 保存的是 `Interrupt` 实例，而不是
    JSON dict。直接走 `_compact_debug_value` 会退化成字符串，导致 response schema
    校验失败，也会让调试台失去 request_id/options 等关键排查信息。
    """

    payload = {
        "type": type(interrupt).__name__,
        "value": _compact_debug_value(getattr(interrupt, "value", interrupt)),
    }
    interrupt_id = getattr(interrupt, "id", None) or getattr(interrupt, "interrupt_id", None)
    if interrupt_id is not None:
        payload["id"] = _compact_debug_value(interrupt_id)
    namespace = getattr(interrupt, "ns", None)
    if namespace is not None:
        payload["namespace"] = _compact_debug_value(namespace)
    return payload


def _compact_debug_object(value) -> dict:
    compacted = _compact_debug_value(value)
    return compacted if isinstance(compacted, dict) else {"value": compacted}


def _compact_debug_value(value, *, depth: int = 0):
    """把 checkpoint state 转成 JSON 兼容调试值。

    checkpoint history 可能包含很长的 prompt、chunk、消息流和非 JSON 原生对象。
    这里做轻量裁剪，保证 API 可用，同时保留定位问题的字段结构。
    """

    if depth >= 6:
        return _compact_debug_scalar(value)
    if isinstance(value, dict):
        return {str(key): _compact_debug_value(item, depth=depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        limit = 30
        items = [_compact_debug_value(item, depth=depth + 1) for item in list(value)[:limit]]
        if len(value) > limit:
            items.append({"__truncated__": len(value) - limit})
        return items
    return _compact_debug_scalar(value)


def _compact_debug_scalar(value):
    """规整 checkpoint 调试值中的标量。"""

    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        max_length = 5000
        if len(value) > max_length:
            return f"{value[:max_length]}\n...[truncated {len(value) - max_length} chars]"
        return value
    return str(value)


def _highlight_memory_chat_mermaid(mermaid: str, node_statuses: dict[str, str]) -> str:
    """给 LangGraph Mermaid 源码注入状态 class。

    Mermaid 图结构仍然来自 LangGraph，状态染色由业务层维护，这样既可信又方便调试。
    """

    lines = _highlight_graph_mermaid(mermaid, node_statuses).splitlines()
    lines.extend(
        [
            "classDef subgraphNode fill:#eef2ff,stroke:#7c3aed,stroke-width:3px,color:#4c1d95;",
            "class agent,tools subgraphNode;",
        ]
    )
    return "\n".join(lines)


def _highlight_graph_mermaid(mermaid: str, node_statuses: dict[str, str]) -> str:
    """给 Mermaid 源码注入通用状态 class。"""

    lines = [
        mermaid.rstrip(),
        "classDef pendingNode fill:#f8fafc,stroke:#cbd5e1,color:#475569;",
        "classDef runningNode fill:#eff6ff,stroke:#2563eb,stroke-width:3px,color:#1d4ed8;",
        "classDef succeededNode fill:#ecfdf5,stroke:#10b981,stroke-width:2px,color:#047857;",
        "classDef failedNode fill:#fef2f2,stroke:#ef4444,stroke-width:3px,color:#b91c1c;",
        "classDef skippedNode fill:#fffbeb,stroke:#f59e0b,color:#92400e;",
    ]
    class_names = {
        "pending": "pendingNode",
        "running": "runningNode",
        "succeeded": "succeededNode",
        "failed": "failedNode",
        "skipped": "skippedNode",
        "interrupted": "skippedNode",
    }
    for node_name, node_status in node_statuses.items():
        class_name = class_names.get(node_status)
        if class_name:
            lines.append(f"class {node_name} {class_name};")
    return "\n".join(lines)

def _get_turn_or_404(session: Session, turn_id: int) -> ChatTurn:
    turn = session.get(ChatTurn, turn_id)
    if turn is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat turn not found")
    return turn


def _decode_json_object(value: str) -> dict[str, str]:
    try:
        payload = json.loads(value or "{}")
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        return {}


def _decode_json_list(value: str) -> list[dict]:
    try:
        payload = json.loads(value or "[]")
        return payload if isinstance(payload, list) else []
    except json.JSONDecodeError:
        return []


def _decode_json_any(value: str, *, fallback):
    try:
        return json.loads(value or "")
    except json.JSONDecodeError:
        return fallback


def _pending_interrupt_from_turn(turn: ChatTurn) -> dict | None:
    payload = _decode_json_any(turn.debug_payload, fallback={})
    if not isinstance(payload, dict):
        return None
    pending = payload.get("pending_interrupt")
    return pending if isinstance(pending, dict) else None
