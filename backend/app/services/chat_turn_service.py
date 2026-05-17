import json

from fastapi import HTTPException, status
from sqlmodel import Session, select

from app.agent.graphs.memory_chat.graph import get_memory_chat_graph_mermaid
from app.models.chat_turn import ChatTurn
from app.models.note import utc_now
from app.schemas.chat import ChatTurnGraphRead
from app.schemas.search import NoteSearchResult


MEMORY_CHAT_NODE_ORDER = [
    "load_turn_state",
    "dispatch_context_workers",
    "build_l4_core_memory",
    "build_l3_retrieved_memory",
    "build_l2_summary",
    "build_l1_recent_messages",
    "build_l0_current_input",
    "merge_prompt_context",
    "generate_answer",
    "persist_messages",
]


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
    return _to_chat_turn_graph_read(turn)


def _to_chat_turn_graph_read(turn: ChatTurn) -> ChatTurnGraphRead:
    node_statuses = _decode_json_object(turn.node_statuses)
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
        context_layers=_decode_json_list(turn.context_layers),
        retrieved_chunks=[
            NoteSearchResult(**chunk) for chunk in _decode_json_list(turn.retrieved_chunks)
        ],
        debug_payload=_decode_json_any(turn.debug_payload, fallback={}),
        error=turn.error,
    )


def _highlight_memory_chat_mermaid(mermaid: str, node_statuses: dict[str, str]) -> str:
    """给 LangGraph Mermaid 源码注入状态 class。

    Mermaid 图结构仍然来自 LangGraph，状态染色由业务层维护，这样既可信又方便调试。
    """

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
