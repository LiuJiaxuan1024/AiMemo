from collections.abc import Callable
from contextlib import AbstractContextManager
import json
from time import perf_counter

from sqlmodel import Session, desc, select

from app.agent.graphs.memory_chat.graph import run_memory_chat_graph, stream_memory_chat_graph
from app.core.config import settings
from app.core.database import session_scope
from app.models.chat_message import ChatMessage
from app.models.conversation import Conversation
from app.models.note import utc_now
from app.rag.chunking.tokenizer import count_tokens
from app.schemas.chat import ChatResponse
from app.schemas.conversation import ChatMessageRead
from app.schemas.search import NoteSearchResult
from app.services.chat_turn_service import (
    attach_chat_turn_messages,
    complete_chat_turn,
    create_running_chat_turn,
    fail_chat_turn,
    initial_node_statuses,
    update_chat_turn_progress,
)
from app.services.conversation_summary_service import enqueue_conversation_summary_job_if_needed
from app.services.conversation_service import _to_chat_message_read
from app.services.long_term_memory_service import enqueue_conversation_memory_job_if_needed


SessionFactory = Callable[[], AbstractContextManager[Session]]


def run_conversation_chat(
    conversation_id: int,
    *,
    message: str,
    session_factory: SessionFactory = session_scope,
    checkpoint_path: str | None = None,
) -> ChatResponse:
    """执行一轮对话并返回业务消息。

    参数：
      conversation_id: 业务对话 ID。
      message: 用户本轮输入。
      session_factory: graph 节点使用的 session 工厂，测试可替换。
      checkpoint_path: LangGraph checkpoint 数据库路径，默认使用配置。
    """

    with session_factory() as session:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            from fastapi import HTTPException, status

            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found",
            )

    result = run_memory_chat_graph(
        conversation_id=conversation_id,
        user_message=message,
        session_factory=session_factory,
        checkpoint_path=checkpoint_path or settings.langgraph_checkpoint_path,
    )
    user_message_id = int(result["user_message_id"])
    assistant_message_id = int(result["assistant_message_id"])
    with session_factory() as session:
        user = _get_message_or_error(session, user_message_id)
        assistant = _get_message_or_error(session, assistant_message_id)
        # 摘要更新不阻塞主回答：这里只负责在达到阈值时创建后台 job。
        # 真正的 LLM 摘要由 conversation_summary_graph 在 worker 中完成。
        enqueue_conversation_summary_job_if_needed(session, conversation_id)
        # 长期记忆抽取同样异步执行。抽取 graph 会自己判断是否真的值得写入 L4。
        enqueue_conversation_memory_job_if_needed(
            session,
            conversation_id=conversation_id,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
        )
        session.commit()
        return ChatResponse(
            conversation_id=conversation_id,
            thread_id=f"conversation:{conversation_id}",
            checkpoint_id=result.get("graph_checkpoint_id"),
            needs_retrieval=bool(result.get("needs_retrieval", False)),
            needs_query_rewrite=bool(result.get("needs_query_rewrite", False)),
            retrieval_query=result.get("retrieval_query", ""),
            retrieval_grade=result.get("retrieval_grade", "none"),
            retrieval_grade_reason=result.get("retrieval_grade_reason", ""),
            retrieval_reason=result.get("retrieval_reason", ""),
            user_message=_to_chat_message_read(user),
            assistant_message=_to_chat_message_read(assistant),
            retrieved_chunks=[
                NoteSearchResult(
                    note_id=chunk["note_id"],
                    note_title=chunk["note_title"],
                    chunk_id=chunk["chunk_id"],
                    chunk_index=chunk["chunk_index"],
                    content=chunk["content"],
                    content_hash=chunk["content_hash"],
                    token_count=chunk["token_count"],
                    distance=chunk["distance"],
                    score=chunk["score"],
                )
                for chunk in result.get("retrieved_chunks", [])
            ],
        )


def stream_conversation_chat_events(
    conversation_id: int,
    *,
    message: str,
    session_factory: SessionFactory = session_scope,
    checkpoint_path: str | None = None,
):
    """生成一轮聊天的 SSE 事件。

    参数：
      conversation_id: 业务会话 ID。
      message: 用户本轮输入。
      session_factory: 数据库 session 工厂，测试可替换。
      checkpoint_path: LangGraph checkpoint 数据库路径。

    事件：
      - turn: 创建 graph_run_id，前端可立即建立调试入口。
      - node: 某个 LangGraph 节点完成，更新流程图状态。
      - answer_delta: generate_answer 节点产生的 LLM token。
      - done: 返回完整 ChatResponse 与 turn_id。
      - error: graph 失败。
    """

    started_at = perf_counter()
    debug_payload = _create_debug_payload()

    with session_factory() as session:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            from fastapi import HTTPException, status

            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found",
            )
        user_message, assistant_message = _create_streaming_message_pair(
            session,
            conversation=conversation,
            user_content=message,
        )
        turn = create_running_chat_turn(
            session,
            conversation_id=conversation_id,
            thread_id=f"conversation:{conversation_id}",
        )
        turn_id = turn.id or 0
        attach_chat_turn_messages(
            session,
            turn_id,
            user_message_id=user_message.id or 0,
            assistant_message_id=assistant_message.id or 0,
        )
        # SQLAlchemy 默认会在 commit 后过期 ORM 对象；在 session 内转换成 schema，
        # 避免 SSE 首包在 session 关闭后访问 detached 对象。
        user_message_read = _to_chat_message_read(user_message)
        assistant_message_read = _to_chat_message_read(assistant_message)
        user_message_id = user_message_read.id
        assistant_message_id = assistant_message_read.id

    node_statuses = initial_node_statuses()
    _mark_debug_event(debug_payload, started_at, "turn_created")
    yield _sse(
        "turn",
        {
            "turn_id": turn_id,
            "user_message": user_message_read.model_dump(mode="json"),
            "assistant_message": assistant_message_read.model_dump(mode="json"),
            "node_statuses": node_statuses,
        },
    )

    try:
        final_state = None
        assistant_content = ""
        for event in stream_memory_chat_graph(
            conversation_id=conversation_id,
            user_message=message,
            session_factory=session_factory,
            checkpoint_path=checkpoint_path or settings.langgraph_checkpoint_path,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
        ):
            if event["event"] == "node":
                node_name = str(event["node"])
                state = event.get("state") if isinstance(event.get("state"), dict) else {}
                _mark_node_succeeded(node_statuses, node_name)
                _mark_node_timing(
                    debug_payload,
                    started_at,
                    node_name=node_name,
                    status="succeeded",
                    state=state,
                )
                with session_factory() as session:
                    update_chat_turn_progress(
                        session,
                        turn_id,
                        node_statuses=node_statuses,
                        debug_payload=debug_payload,
                    )
                yield _sse("node", {"node": node_name, "node_statuses": node_statuses})
            elif event["event"] == "answer_delta":
                # token 到达时，generate_answer 已经在执行中。updates 事件要等节点完成后
                # 才会出现，所以这里主动把节点标记为 running，避免前端看到回答在流动、
                # 但 graph 图还停在上一个节点。
                node_name = str(event.get("node") or "generate_answer")
                should_emit_node = node_statuses.get(node_name) != "running"
                _mark_node_running(node_statuses, node_name)
                _mark_answer_token_timing(debug_payload, started_at)
                if should_emit_node:
                    _mark_node_timing(
                        debug_payload,
                        started_at,
                        node_name=node_name,
                        status="running",
                    )
                    with session_factory() as session:
                        update_chat_turn_progress(
                            session,
                            turn_id,
                            node_statuses=node_statuses,
                            debug_payload=debug_payload,
                        )
                    yield _sse("node", {"node": node_name, "node_statuses": node_statuses})
                delta = str(event.get("content") or "")
                assistant_content += delta
                with session_factory() as session:
                    _update_streaming_assistant_message(
                        session,
                        assistant_message_id=assistant_message_id,
                        content=assistant_content,
                        status="streaming",
                    )
                yield _sse("answer_delta", {"content": delta})
            elif event["event"] == "internal_token":
                # 内部 LLM token 例如 planner JSON，默认不暴露给前端。
                # 后续如果做“调试模式”，可以在这里转成 internal_token SSE。
                continue
            elif event["event"] == "done":
                final_state = event["state"]
                _mark_debug_event(debug_payload, started_at, "graph_done")

        if final_state is None:
            raise RuntimeError("Memory chat graph finished without final state.")

        user_message_id = int(final_state["user_message_id"])
        assistant_message_id = int(final_state["assistant_message_id"])
        checkpoint_id = final_state.get("graph_checkpoint_id")
        for node_name in node_statuses:
            if node_statuses[node_name] == "running":
                node_statuses[node_name] = "succeeded"
            if node_statuses[node_name] == "pending":
                node_statuses[node_name] = "skipped"

        _mark_debug_event(debug_payload, started_at, "turn_completed")
        context_layers = _extract_context_layers(final_state)
        retrieved_chunks = list(final_state.get("retrieved_chunks", []))
        final_assistant_content = str(final_state.get("assistant_answer") or assistant_content)
        debug_payload["summary"]["answer_chars"] = len(final_assistant_content)
        debug_payload["summary"]["retrieved_count"] = len(retrieved_chunks)
        with session_factory() as session:
            _update_streaming_assistant_message(
                session,
                assistant_message_id=assistant_message_id,
                content=final_assistant_content,
                status="completed",
            )
            complete_chat_turn(
                session,
                turn_id,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
                checkpoint_id=checkpoint_id,
                node_statuses=node_statuses,
                context_layers=context_layers,
                retrieved_chunks=retrieved_chunks,
                debug_payload=debug_payload,
            )
            user = _get_message_or_error(session, user_message_id)
            assistant = _get_message_or_error(session, assistant_message_id)
            enqueue_conversation_summary_job_if_needed(session, conversation_id)
            enqueue_conversation_memory_job_if_needed(
                session,
                conversation_id=conversation_id,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
            )
            session.commit()
            response = ChatResponse(
                conversation_id=conversation_id,
                thread_id=f"conversation:{conversation_id}",
                checkpoint_id=checkpoint_id,
                needs_retrieval=bool(final_state.get("needs_retrieval", False)),
                needs_query_rewrite=bool(final_state.get("needs_query_rewrite", False)),
                retrieval_query=final_state.get("retrieval_query", ""),
                retrieval_grade=final_state.get("retrieval_grade", "none"),
                retrieval_grade_reason=final_state.get("retrieval_grade_reason", ""),
                retrieval_reason=final_state.get("retrieval_reason", ""),
                user_message=_to_chat_message_read(user),
                assistant_message=_to_chat_message_read(assistant),
                retrieved_chunks=[
                    NoteSearchResult(
                        note_id=chunk["note_id"],
                        note_title=chunk["note_title"],
                        chunk_id=chunk["chunk_id"],
                        chunk_index=chunk["chunk_index"],
                        content=chunk["content"],
                        content_hash=chunk["content_hash"],
                        token_count=chunk["token_count"],
                        distance=chunk["distance"],
                        score=chunk["score"],
                    )
                    for chunk in retrieved_chunks
                ],
            )
        yield _sse("done", {"turn_id": turn_id, "response": response.model_dump(mode="json")})
    except Exception as exc:
        for node_name, node_status in list(node_statuses.items()):
            if node_status == "running":
                node_statuses[node_name] = "failed"
                _mark_node_timing(
                    debug_payload,
                    started_at,
                    node_name=node_name,
                    status="failed",
                )
        _mark_debug_event(debug_payload, started_at, "turn_failed")
        with session_factory() as session:
            _update_streaming_assistant_message(
                session,
                assistant_message_id=assistant_message_id,
                content=assistant_content,
                status="failed",
            )
            fail_chat_turn(
                session,
                turn_id,
                node_statuses=node_statuses,
                error=str(exc),
                debug_payload=debug_payload,
            )
        yield _sse("error", {"turn_id": turn_id, "message": str(exc), "node_statuses": node_statuses})


def _get_message_or_error(session: Session, message_id: int) -> ChatMessage:
    message = session.get(ChatMessage, message_id)
    if message is None:
        raise RuntimeError(f"ChatMessage {message_id} was not found after graph execution.")
    return message


def _sse(event: str, data: dict) -> str:
    """把事件编码为浏览器 EventSource/fetch 可读取的 SSE 文本。"""

    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _mark_node_running(node_statuses: dict[str, str], node_name: str) -> None:
    """把当前节点标记为 running。

    LangGraph 的 updates 事件表示“节点已经产出更新”，messages 事件表示
    “LLM 节点正在产出 token”。并行 worker 可能同时运行，所以这里不能把其他
    running 节点强行收敛为 succeeded。
    """

    if node_name in node_statuses:
        node_statuses[node_name] = "running"


def _mark_node_succeeded(node_statuses: dict[str, str], node_name: str) -> None:
    """把 updates 事件对应节点标记为 succeeded。

    LangGraph 的 updates chunk 是节点完成后的状态更新，不是节点开始事件。
    因此收到 updates 时应直接标记成功，避免并行 worker 图出现错误的 running 状态。
    """

    if node_name in node_statuses:
        node_statuses[node_name] = "succeeded"


def _extract_context_layers(state: dict) -> list[dict]:
    """从 graph state 中提取 L0-L4 上下文层，保持从高层记忆到当前输入的顺序。"""

    layers = []
    for key in [
        "context_l4_layer",
        "context_l3_layer",
        "context_l2_layer",
        "context_l1_layer",
        "context_l0_layer",
    ]:
        payload = state.get(key)
        if payload:
            layers.append(dict(payload))
    return layers


def _create_debug_payload() -> dict:
    """创建单轮对话性能埋点结构。

    所有时间均以 turn 开始为基准，单位为毫秒。该结构会写入 ChatTurn.debug_payload，
    供 graph 调试面板和后续性能分析使用。
    """

    return {
        "version": 1,
        "events": {},
        "nodes": {},
        "summary": {
            "first_answer_token_ms": None,
            "last_answer_token_ms": None,
            "answer_token_events": 0,
            "answer_chars": 0,
            "retrieved_count": 0,
        },
    }


def _mark_debug_event(debug_payload: dict, started_at: float, event_name: str) -> None:
    """记录一个 turn 级事件时间点。"""

    debug_payload.setdefault("events", {})[event_name] = _elapsed_ms_since(started_at)


def _mark_node_timing(
    debug_payload: dict,
    started_at: float,
    *,
    node_name: str,
    status: str,
    state: dict | None = None,
) -> None:
    """记录节点状态和完成时间。

    LangGraph updates 是节点完成事件；answer_delta 到达时 generate_answer 处于 running。
    L3 的内部耗时来自 graph state.retrieval_debug，会在这里并入节点记录。
    """

    node_payload = debug_payload.setdefault("nodes", {}).setdefault(node_name, {})
    node_payload["status"] = status
    if status == "running" and "started_ms" not in node_payload:
        node_payload["started_ms"] = _elapsed_ms_since(started_at)
    if status in {"succeeded", "failed"}:
        node_payload["completed_ms"] = _elapsed_ms_since(started_at)
        started_ms = node_payload.get("started_ms")
        if isinstance(started_ms, int):
            node_payload["duration_ms"] = node_payload["completed_ms"] - started_ms
    if state and node_name == "build_l3_retrieved_memory":
        retrieval_debug = state.get("retrieval_debug")
        if isinstance(retrieval_debug, dict):
            node_payload["retrieval_debug"] = retrieval_debug


def _mark_answer_token_timing(debug_payload: dict, started_at: float) -> None:
    """记录回答 token 的首包和最新 token 时间。"""

    summary = debug_payload.setdefault("summary", {})
    current_ms = _elapsed_ms_since(started_at)
    if summary.get("first_answer_token_ms") is None:
        summary["first_answer_token_ms"] = current_ms
    summary["last_answer_token_ms"] = current_ms
    summary["answer_token_events"] = int(summary.get("answer_token_events") or 0) + 1


def _elapsed_ms_since(started_at: float) -> int:
    return int((perf_counter() - started_at) * 1000)


def _create_streaming_message_pair(
    session: Session,
    *,
    conversation: Conversation,
    user_content: str,
) -> tuple[ChatMessage, ChatMessage]:
    """在 graph 启动前创建本轮用户消息和 assistant 草稿。

    参数：
      session: 当前数据库会话。
      conversation: 业务会话对象。
      user_content: 用户本轮输入。

    返回：
      已落库的一问一答消息。这样浏览器刷新后，UI 仍能从消息表恢复当前轮次。
    """

    if conversation.id is None:
        raise RuntimeError("Conversation id is required before creating chat messages.")
    parent_id = _latest_message_id(session, conversation.id)
    user = ChatMessage(
        conversation_id=conversation.id,
        role="user",
        content=user_content,
        parent_id=parent_id,
        status="completed",
        token_count=count_tokens(user_content),
    )
    session.add(user)
    session.flush()
    if user.id is None:
        raise RuntimeError("User message id was not generated.")

    assistant = ChatMessage(
        conversation_id=conversation.id,
        role="assistant",
        content="",
        parent_id=user.id,
        status="streaming",
        token_count=0,
    )
    session.add(assistant)
    conversation.updated_at = utc_now()
    session.add(conversation)
    session.commit()
    session.refresh(user)
    session.refresh(assistant)
    return user, assistant


def _update_streaming_assistant_message(
    session: Session,
    *,
    assistant_message_id: int,
    content: str,
    status: str,
) -> None:
    """更新流式 assistant 草稿内容。

    参数：
      session: 当前数据库会话。
      assistant_message_id: graph 启动前创建的 assistant 消息 ID。
      content: 当前已经生成的完整文本。
      status: streaming / completed / failed。
    """

    if not assistant_message_id:
        return
    message = session.get(ChatMessage, assistant_message_id)
    if message is None:
        return
    message.content = content
    message.status = status
    message.token_count = count_tokens(content) if content else 0
    message.updated_at = utc_now()
    session.add(message)
    session.commit()


def _latest_message_id(session: Session, conversation_id: int) -> int | None:
    message = session.exec(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(desc(ChatMessage.created_at), desc(ChatMessage.id))
    ).first()
    return message.id if message else None
