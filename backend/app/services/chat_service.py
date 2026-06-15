from collections.abc import Callable
from contextlib import AbstractContextManager
import json
import logging
import traceback
import threading
from time import perf_counter

from fastapi import HTTPException, status
from sqlmodel import Session, desc, select

from app.core.config import settings
from app.core.database import session_scope
from app.models.chat_message import ChatMessage
from app.models.chat_turn import ChatTurn
from app.models.conversation import Conversation
from app.models.note import utc_now
from app.rag.chunking.tokenizer import count_tokens
from app.schemas.chat import ChatResponse
from app.schemas.elf import ElfEventCreate
from app.schemas.conversation import ChatMessageRead
from app.schemas.search import NoteSearchResult
from app.services import chat_turn_buffer
from app.services.attachment_service import attach_attachments_to_message, list_message_attachments
from app.services.chat_turn_service import (
    attach_chat_turn_messages,
    complete_chat_turn,
    create_running_chat_turn,
    fail_chat_turn,
    interrupt_chat_turn,
    initial_node_statuses,
    mark_chat_turn_resuming,
    update_chat_turn_progress,
)
from app.services.conversation_summary_service import enqueue_conversation_summary_job_if_needed
from app.services.conversation_title_service import enqueue_conversation_title_job_if_needed
from app.services.conversation_service import _to_chat_message_read
from app.services.elf_event_service import emit_elf_event
from app.services.long_term_memory_service import enqueue_conversation_memory_job_if_needed


logger = logging.getLogger(__name__)


SessionFactory = Callable[[], AbstractContextManager[Session]]


class ChatTurnCancelled(RuntimeError):
    """Raised inside a worker when the user has cancelled the turn."""


def run_conversation_chat(
    conversation_id: int,
    *,
    message: str,
    attachment_ids: list[int] | None = None,
    parent_message_id: int | None = None,
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

    attachment_ids = attachment_ids or []
    message = _normalize_chat_message_for_attachments(message, attachment_ids)

    with session_factory() as session:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            from fastapi import HTTPException, status

            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found",
            )
        langgraph_thread_id = (
            conversation.langgraph_thread_id or f"conversation:{conversation_id}"
        )

    from app.agent.graphs.memory_chat.graph import run_memory_chat_graph

    result = run_memory_chat_graph(
        conversation_id=conversation_id,
        user_message=message,
        session_factory=session_factory,
        checkpoint_path=checkpoint_path or settings.langgraph_checkpoint_path,
        parent_message_id=parent_message_id,
        langgraph_thread_id=langgraph_thread_id,
        attachment_ids=attachment_ids or [],
    )
    user_message_id = int(result["user_message_id"])
    assistant_message_id = int(result["assistant_message_id"])
    with session_factory() as session:
        user = _get_message_or_error(session, user_message_id)
        assistant = _get_message_or_error(session, assistant_message_id)
        attachments_by_message_id = list_message_attachments(
            session,
            conversation_id=conversation_id,
            message_ids=[user_message_id, assistant_message_id],
        )
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
        # 自动给新会话起标题：title 还是默认值时入队一个一次性 LLM job。
        enqueue_conversation_title_job_if_needed(session, conversation_id)
        session.commit()
        return ChatResponse(
            conversation_id=conversation_id,
            thread_id=langgraph_thread_id,
            checkpoint_id=result.get("graph_checkpoint_id"),
            needs_retrieval=bool(result.get("needs_retrieval", False)),
            needs_query_rewrite=bool(result.get("needs_query_rewrite", False)),
            retrieval_query=result.get("retrieval_query", ""),
            retrieval_grade=result.get("retrieval_grade", "none"),
            retrieval_grade_reason=result.get("retrieval_grade_reason", ""),
            retrieval_reason=result.get("retrieval_reason", ""),
            user_message=_to_chat_message_read(
                user,
                attachments=attachments_by_message_id.get(user.id or 0, []),
            ),
            assistant_message=_to_chat_message_read(
                assistant,
                attachments=attachments_by_message_id.get(assistant.id or 0, []),
            ),
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
    attachment_ids: list[int] | None = None,
    parent_message_id: int | None = None,
    session_factory: SessionFactory = session_scope,
    checkpoint_path: str | None = None,
    emit_status_events: bool = True,
    answer_mode: str = "text",
    runtime_scope: str = "page",
):
    """生成一轮聊天的 SSE 事件。

    参数：
      conversation_id: 业务会话 ID。
      message: 用户本轮输入。
      session_factory: 数据库 session 工厂，测试可替换。
      checkpoint_path: LangGraph checkpoint 数据库路径。
      emit_status_events: 是否向精灵事件中心播报“开始思考/开始回答/完成”等状态。
        桌面精灵外置聊天会关闭它，因为用户正在直接和精灵对话，不需要额外工作播报。
      answer_mode: 回答生成模式。text 走 ReAct agent；elf_bubble 走气泡回答分支。

    事件：
      - turn: 创建 graph_run_id，前端可立即建立调试入口。
      - node: 某个 LangGraph 节点完成，更新流程图状态。
      - answer_delta: agent 节点产生的最终回答 token。
      - done: 返回完整 ChatResponse 与 turn_id。
      - error: graph 失败。
    """

    attachment_ids = attachment_ids or []
    message = _normalize_chat_message_for_attachments(message, attachment_ids)

    started_at = perf_counter()
    debug_payload = _create_debug_payload()
    graph_variant = "elf" if answer_mode == "elf_bubble" else "page"

    with session_factory() as session:
        conversation = session.get(Conversation, conversation_id)
        if conversation is None:
            from fastapi import HTTPException, status

            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="Conversation not found",
            )
        # 用 conversation 行里的 langgraph_thread_id（新会话会带 uuid 后缀），避免 SQLite
        # 整型 id 被删除复用时新会话误读到旧 checkpoint。
        langgraph_thread_id = conversation.langgraph_thread_id or f"conversation:{conversation_id}"
        user_message, assistant_message = _create_streaming_message_pair(
            session,
            conversation=conversation,
            user_content=message,
            parent_message_id=parent_message_id,
        )
        user_attachments = attach_attachments_to_message(
            session,
            conversation_id=conversation_id,
            message_id=user_message.id or 0,
            attachment_ids=attachment_ids or [],
        )
        if user_attachments:
            session.commit()
        turn = create_running_chat_turn(
            session,
            conversation_id=conversation_id,
            thread_id=langgraph_thread_id,
            graph_variant=graph_variant,
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
        attachments_by_message_id = list_message_attachments(
            session,
            conversation_id=conversation_id,
            message_ids=[user_message.id or 0, assistant_message.id or 0],
        )
        user_message_read = _to_chat_message_read(
            user_message,
            attachments=attachments_by_message_id.get(user_message.id or 0, []),
        )
        assistant_message_read = _to_chat_message_read(assistant_message, turn_id=turn_id)
        user_message_id = user_message_read.id
        assistant_message_id = assistant_message_read.id

    node_statuses = initial_node_statuses(graph_variant=graph_variant)
    _mark_debug_event(debug_payload, started_at, "turn_created")
    if runtime_scope == "elf":
        _update_elf_runtime(
            session_factory,
            status="thinking",
            conversation_id=conversation_id,
            turn_id=turn_id,
            pending_interrupt={},
            last_message=message,
            last_bubbles=[],
            last_error="",
        )
    if emit_status_events:
        emit_elf_event(
            ElfEventCreate(
                source="chat",
                mood="thinking",
                motion="thinking",
                message="我在整理这次问题的上下文。",
                priority=20,
                ttl_ms=3200,
                dedupe_key=f"chat:{turn_id}:started",
                metadata={"conversation_id": conversation_id, "turn_id": turn_id},
            )
        )
    # 把首包 turn 事件先推进 buffer，再把图执行交给后台线程；HTTP 生成器只是 subscriber。
    # 这样浏览器断开/切会话/刷新都不会终止 graph，再次 GET /events/stream 还能从头重放。
    initial_turn_event = _sse(
        "turn",
        {
            "turn_id": turn_id,
            "user_message": user_message_read.model_dump(mode="json"),
            "assistant_message": assistant_message_read.model_dump(mode="json"),
            "node_statuses": node_statuses,
        },
    )
    buffer = chat_turn_buffer.get_or_create(turn_id)
    buffer.append(initial_turn_event)
    chat_turn_buffer.cleanup_expired()

    worker = threading.Thread(
        target=_run_turn_to_buffer,
        kwargs={
            "buffer": buffer,
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "message": message,
            "session_factory": session_factory,
            "checkpoint_path": checkpoint_path or settings.langgraph_checkpoint_path,
            "emit_status_events": emit_status_events,
            "answer_mode": answer_mode,
            "user_message_id": user_message_id,
            "assistant_message_id": assistant_message_id,
            "parent_message_id": parent_message_id,
            "node_statuses": node_statuses,
            "debug_payload": debug_payload,
            "started_at": started_at,
            "resume_payload": None,
            "langgraph_thread_id": langgraph_thread_id,
            "attachment_ids": attachment_ids or [],
            "runtime_scope": runtime_scope,
        },
        daemon=True,
        name=f"chat-turn-{turn_id}",
    )
    worker.start()

    yield from buffer.subscribe(from_index=0)


def stream_conversation_chat_resume_events(
    conversation_id: int,
    turn_id: int,
    *,
    resume_payload: dict,
    session_factory: SessionFactory = session_scope,
    checkpoint_path: str | None = None,
    emit_status_events: bool = True,
    answer_mode: str = "text",
    runtime_scope: str = "page",
):
    """恢复一条因 request_user_input 中断的聊天 turn。"""

    started_at = perf_counter()
    with session_factory() as session:
        turn = session.get(ChatTurn, turn_id)
        if turn is None or turn.conversation_id != conversation_id:
            from fastapi import HTTPException, status

            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Chat turn not found")
        if turn.status != "interrupted":
            from fastapi import HTTPException, status

            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Chat turn is not interrupted")
        user_message = _get_message_or_error(session, int(turn.user_message_id or 0))
        assistant_message = _get_message_or_error(session, int(turn.assistant_message_id or 0))
        node_statuses = _decode_json_object(turn.node_statuses)
        node_statuses["tools"] = "running"
        debug_payload = _decode_json_any(turn.debug_payload, fallback=_create_debug_payload())
        if not isinstance(debug_payload, dict):
            debug_payload = _create_debug_payload()
        _mark_debug_event(debug_payload, started_at, "turn_resumed")
        mark_chat_turn_resuming(
            session,
            turn_id,
            node_statuses=node_statuses,
            debug_payload=debug_payload,
        )
        _update_streaming_assistant_message(
            session,
            assistant_message_id=assistant_message.id or 0,
            content=assistant_message.content,
            status="streaming",
        )
        user_message_id = user_message.id or 0
        assistant_message_id = assistant_message.id or 0
        original_user_content = user_message.content
        # resume 走的 LangGraph thread_id 必须和最初那一轮一致，否则 Command(resume=...)
        # 会落到一个空 thread 上，interrupt 状态找不到，graph 会从头跑。
        conversation = session.get(Conversation, conversation_id)
        resume_thread_id = (
            (conversation.langgraph_thread_id if conversation else None)
            or turn.thread_id
            or f"conversation:{conversation_id}"
        )

    buffer = chat_turn_buffer.create_fresh(turn_id)
    if runtime_scope == "elf":
        _update_elf_runtime(
            session_factory,
            status="thinking",
            conversation_id=conversation_id,
            turn_id=turn_id,
            pending_interrupt={},
            last_error="",
        )
    buffer.append(
        _sse(
            "resume",
            {
                "turn_id": turn_id,
                "node_statuses": node_statuses,
            },
        )
    )
    worker = threading.Thread(
        target=_run_turn_to_buffer,
        kwargs={
            "buffer": buffer,
            "conversation_id": conversation_id,
            "turn_id": turn_id,
            "message": original_user_content,
            "session_factory": session_factory,
            "checkpoint_path": checkpoint_path or settings.langgraph_checkpoint_path,
            "emit_status_events": emit_status_events,
            "answer_mode": answer_mode,
            "user_message_id": user_message_id,
            "assistant_message_id": assistant_message_id,
            "parent_message_id": None,
            "node_statuses": node_statuses,
            "debug_payload": debug_payload,
            "started_at": started_at,
            "resume_payload": resume_payload,
            "langgraph_thread_id": resume_thread_id,
            "runtime_scope": runtime_scope,
        },
        daemon=True,
        name=f"chat-turn-resume-{turn_id}",
    )
    worker.start()
    yield from buffer.subscribe(from_index=0)


def stream_existing_turn_events(turn_id: int):
    """SSE 重连入口：让前端在切回会话/刷新后重新拿到正在跑或刚跑完的事件流。

    - 如果 buffer 还在（turn 在跑、或完成但未过 retention），从头重放完整事件流。
    - 如果 buffer 已经被回收，立刻 yield 一个 `turn_unavailable` 通知并关闭 SSE；
      前端可以靠普通的 listMessages 拿到落库的最终 assistant 消息。
    """

    buffer = chat_turn_buffer.get(turn_id)
    if buffer is None:
        yield _sse(
            "turn_unavailable",
            {
                "turn_id": turn_id,
                "reason": "buffer_expired_or_unknown",
            },
        )
        return
    yield from buffer.subscribe(from_index=0)


def _run_turn_to_buffer(
    *,
    buffer: "chat_turn_buffer.TurnBuffer",
    conversation_id: int,
    turn_id: int,
    message: str,
    session_factory: SessionFactory,
    checkpoint_path: str,
    emit_status_events: bool,
    answer_mode: str,
    user_message_id: int,
    assistant_message_id: int,
    parent_message_id: int | None,
    node_statuses: dict[str, str],
    debug_payload: dict,
    started_at: float,
    resume_payload: dict | None = None,
    langgraph_thread_id: str | None = None,
    attachment_ids: list[int] | None = None,
    runtime_scope: str = "page",
) -> None:
    """Graph worker：在后台线程里跑完一轮 memory_chat_graph，事件全部推进 buffer。

    成功路径写 done 事件；任何异常写 error 事件；无论哪条退出路径都必须
    `buffer.mark_done()`，否则 subscriber 会被永远阻塞在 cond.wait。
    """

    final_state = None
    assistant_content = ""
    if resume_payload is not None:
        try:
            with session_factory() as session:
                existing_assistant = session.get(ChatMessage, assistant_message_id)
                if existing_assistant is not None:
                    assistant_content = existing_assistant.content or ""
        except Exception:
            logger.exception("failed to load existing assistant content for resumed turn %s", turn_id)
    # 这一轮里"当前 ReAct 步号"——agent 节点把 step_index 写到自己的 state_update 里，
    # 我们用它给随后的 answer_delta 打标，让前端把同一段思考-工具-文本聚到一段 segment。
    current_step_index = 0
    # tool_invocation 用 tool_call_id 去重：custom event 先到，state_update 兜底时
    # 不应再次派发同一条卡片；同时也覆盖 stream_writer 失败时只能从 state 派发的情形。
    emitted_tool_call_ids: set[str] = set()
    last_runtime_status = "thinking" if runtime_scope == "elf" else ""

    from app.agent.graphs.memory_chat.graph import stream_memory_chat_graph

    def mark_elf_runtime(status: str, **kwargs) -> None:
        nonlocal last_runtime_status
        if runtime_scope != "elf":
            return
        if status == last_runtime_status and not kwargs:
            return
        last_runtime_status = status
        _update_elf_runtime(
            session_factory,
            status=status,
            conversation_id=conversation_id,
            turn_id=turn_id,
            **kwargs,
        )

    try:
        for event in stream_memory_chat_graph(
            conversation_id=conversation_id,
            user_message=message,
            session_factory=session_factory,
            checkpoint_path=checkpoint_path,
            user_message_id=user_message_id,
            assistant_message_id=assistant_message_id,
            parent_message_id=parent_message_id,
            answer_mode=answer_mode,
            resume_payload=resume_payload,
            langgraph_thread_id=langgraph_thread_id,
            attachment_ids=attachment_ids or [],
        ):
            if _is_chat_turn_cancelled(session_factory, turn_id):
                raise ChatTurnCancelled("用户中断了本轮生成。")
            if event["event"] == "node":
                node_name = str(event["node"])
                state = event.get("state") if isinstance(event.get("state"), dict) else {}
                # agent 节点会回写 agent_step_index——同步给本作用域以便给随后的 answer_delta 打标。
                state_step_index = state.get("agent_step_index")
                if isinstance(state_step_index, int) and state_step_index > current_step_index:
                    current_step_index = state_step_index
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
                buffer.append(_sse("node", {"node": node_name, "node_statuses": node_statuses}))
                # tools 节点状态更新到达时，对 custom event 还没覆盖过的 observation 兜底派发。
                # 正常路径下每条 observation 在 _invoke_one 之后立刻通过 custom event 派发了；
                # 这一段只是为了不让 stream_writer 异常的极端情况丢卡片。
                if node_name == "tools":
                    observations = state.get("tool_observations")
                    if isinstance(observations, list):
                        for observation in observations:
                            if not isinstance(observation, dict):
                                continue
                            tool_call_id = str(observation.get("tool_call_id") or "")
                            if tool_call_id and tool_call_id in emitted_tool_call_ids:
                                continue
                            buffer.append(
                                _sse(
                                    "tool_invocation",
                                    _build_tool_invocation_payload(
                                        observation,
                                        step_index=current_step_index,
                                    ),
                                )
                            )
                            if tool_call_id:
                                emitted_tool_call_ids.add(tool_call_id)
            elif event["event"] == "tool_invocation":
                # tools 节点 _invoke_one 在工具执行前/后各 push 一次：
                #   - running=True：让前端立刻以 running 态 mount 卡片，产生 pending 动画。
                #   - running=False：覆盖同一个 tool_call_id 的卡片为完成/失败态。
                # 同一 tool_call_id 在 running=False 之前不进 dedupe，确保完成事件能照常派发。
                tool_call_id = str(event.get("tool_call_id") or "")
                running = bool(event.get("running"))
                if running:
                    mark_elf_runtime("tool_running")
                if tool_call_id and not running and tool_call_id in emitted_tool_call_ids:
                    continue
                step_index_value = event.get("step_index")
                step_index = step_index_value if isinstance(step_index_value, int) else current_step_index
                observation = {
                    "tool_call_id": tool_call_id,
                    "tool_name": event.get("tool_name"),
                    "arguments": event.get("arguments") if isinstance(event.get("arguments"), dict) else {},
                    "ok": event.get("ok"),
                    "blocked": event.get("blocked"),
                    "error_code": event.get("error_code"),
                    "message": event.get("message"),
                    "running": running,
                }
                buffer.append(
                    _sse(
                        "tool_invocation",
                        _build_tool_invocation_payload(observation, step_index=int(step_index)),
                    )
                )
                # 只在完成态进 dedupe；这样如果 stream writer 漏发了 running 事件，
                # state_update 兜底仍能把完成态卡片补上。
                if tool_call_id and not running:
                    emitted_tool_call_ids.add(tool_call_id)
            elif event["event"] == "answer_delta":
                # token 到达时，agent 已经在执行中。updates 事件要等节点完成后
                # 才会出现，所以这里主动把节点标记为 running，避免前端看到回答在流动、
                # 但 graph 图还停在上一个节点。
                node_name = str(event.get("node") or "agent")
                should_emit_node = node_statuses.get(node_name) != "running"
                _mark_node_running(node_statuses, node_name)
                _mark_answer_token_timing(debug_payload, started_at)
                if should_emit_node:
                    if emit_status_events:
                        emit_elf_event(
                            ElfEventCreate(
                                source="chat",
                                mood="talking",
                                motion="nod",
                                message="我开始回答了。",
                                priority=35,
                                ttl_ms=2500,
                                dedupe_key=f"chat:{turn_id}:answer-started",
                                metadata={"conversation_id": conversation_id, "turn_id": turn_id},
                            )
                        )
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
                    buffer.append(_sse("node", {"node": node_name, "node_statuses": node_statuses}))
                delta = str(event.get("content") or "")
                assistant_content += delta
                mark_elf_runtime("streaming_answer", last_message=assistant_content)
                with session_factory() as session:
                    _update_streaming_assistant_message(
                        session,
                        assistant_message_id=assistant_message_id,
                        content=assistant_content,
                        status="streaming",
                    )
                buffer.append(
                    _sse(
                        "answer_delta",
                        {"content": delta, "step_index": current_step_index},
                    )
                )
            elif event["event"] == "internal_token":
                # 内部 LLM token 例如 planner JSON，默认不暴露给前端。
                # 后续如果做“调试模式”，可以在这里转成 internal_token SSE。
                continue
            elif event["event"] == "thought_snapshot":
                thoughts = list(event.get("thoughts") or [])
                debug_payload["thoughts"] = thoughts
                with session_factory() as session:
                    update_chat_turn_progress(
                        session,
                        turn_id,
                        node_statuses=node_statuses,
                        debug_payload=debug_payload,
                    )
                buffer.append(
                    _sse(
                        "thought_snapshot",
                        {
                            "node": event.get("node", ""),
                            "thoughts": thoughts,
                        },
                    )
                )
            elif event["event"] == "bubble_delta":
                if answer_mode == "elf_bubble":
                    buffer.append(
                        _sse(
                            "bubble_delta",
                            {
                                "content": event.get("content", ""),
                                "node": event.get("node", ""),
                            },
                        )
                    )
                continue
            elif event["event"] == "interrupt":
                final_state = event.get("state") if isinstance(event.get("state"), dict) else {}
                checkpoint_id = final_state.get("graph_checkpoint_id")
                pending_interrupt = _normalize_interrupt_payload(event.get("interrupt"))
                mark_elf_runtime(
                    "waiting_user_input",
                    pending_interrupt=pending_interrupt,
                    last_message=assistant_content,
                )
                for node_name, node_status in list(node_statuses.items()):
                    if node_status == "running":
                        node_statuses[node_name] = "interrupted"
                    elif node_status == "pending":
                        node_statuses[node_name] = "skipped"
                node_statuses["tools"] = "interrupted"
                _mark_debug_event(debug_payload, started_at, "turn_interrupted")
                debug_payload["pending_interrupt"] = pending_interrupt
                if emit_status_events:
                    emit_elf_event(
                        ElfEventCreate(
                            source="chat",
                            mood="thinking",
                            motion="thinking",
                            message="我需要你做个选择再继续。",
                            priority=45,
                            ttl_ms=4200,
                            dedupe_key=f"chat:{turn_id}:interrupted",
                            metadata={"conversation_id": conversation_id, "turn_id": turn_id},
                        )
                    )
                with session_factory() as session:
                    _update_streaming_assistant_message(
                        session,
                        assistant_message_id=assistant_message_id,
                        content=assistant_content,
                        status="interrupted",
                    )
                    interrupt_chat_turn(
                        session,
                        turn_id,
                        checkpoint_id=str(checkpoint_id) if checkpoint_id else None,
                        node_statuses=node_statuses,
                        pending_interrupt=pending_interrupt,
                        debug_payload=debug_payload,
                    )
                buffer.append(
                    _sse(
                        "interrupt",
                        {
                            "turn_id": turn_id,
                            "request": pending_interrupt,
                            "node_statuses": node_statuses,
                        },
                    )
                )
                return
            elif event["event"] == "done":
                final_state = event["state"]
                _mark_debug_event(debug_payload, started_at, "graph_done")

        if _is_chat_turn_cancelled(session_factory, turn_id):
            raise ChatTurnCancelled("用户中断了本轮生成。")

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
        if emit_status_events:
            emit_elf_event(
                ElfEventCreate(
                    source="chat",
                    mood="success",
                    motion="success",
                    message="这轮对话完成了。",
                    priority=30,
                    ttl_ms=2800,
                    dedupe_key=f"chat:{turn_id}:completed",
                    metadata={"conversation_id": conversation_id, "turn_id": turn_id},
                )
            )
        context_layers = _extract_context_layers(final_state)
        elf_bubbles = list(final_state.get("elf_bubble_answer_parts", []))
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
            attachments_by_message_id = list_message_attachments(
                session,
                conversation_id=conversation_id,
                message_ids=[user_message_id, assistant_message_id],
            )
            enqueue_conversation_summary_job_if_needed(session, conversation_id)
            enqueue_conversation_memory_job_if_needed(
                session,
                conversation_id=conversation_id,
                user_message_id=user_message_id,
                assistant_message_id=assistant_message_id,
            )
            enqueue_conversation_title_job_if_needed(session, conversation_id)
            session.commit()
            response = ChatResponse(
                conversation_id=conversation_id,
                thread_id=langgraph_thread_id or f"conversation:{conversation_id}",
                checkpoint_id=checkpoint_id,
                needs_retrieval=bool(final_state.get("needs_retrieval", False)),
                needs_query_rewrite=bool(final_state.get("needs_query_rewrite", False)),
                retrieval_query=final_state.get("retrieval_query", ""),
                retrieval_grade=final_state.get("retrieval_grade", "none"),
                retrieval_grade_reason=final_state.get("retrieval_grade_reason", ""),
                retrieval_reason=final_state.get("retrieval_reason", ""),
                user_message=_to_chat_message_read(
                    user,
                    attachments=attachments_by_message_id.get(user.id or 0, []),
                ),
                assistant_message=_to_chat_message_read(
                    assistant,
                    attachments=attachments_by_message_id.get(assistant.id or 0, []),
                    turn_id=turn_id,
                ),
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
        done_payload = {"turn_id": turn_id, "response": response.model_dump(mode="json")}
        if answer_mode == "elf_bubble":
            done_payload["bubbles"] = elf_bubbles
        mark_elf_runtime(
            "completed",
            pending_interrupt={},
            last_message=final_assistant_content,
            last_bubbles=elf_bubbles,
            last_error="",
        )
        buffer.append(_sse("done", done_payload))
    except ChatTurnCancelled as exc:
        logger.info("chat turn %s cancelled", turn_id)
        mark_elf_runtime("failed", last_error=str(exc))
        buffer.append(
            _sse(
                "error",
                {
                    "turn_id": turn_id,
                    "message": str(exc),
                    "node_statuses": node_statuses,
                },
            )
        )
    except Exception as exc:
        logger.exception("chat turn %s worker crashed", turn_id)
        exception_detail = _build_exception_detail(exc)
        mark_elf_runtime("failed", last_error=exception_detail["message"])
        debug_payload.setdefault("diagnostics", []).append(
            {
                "code": "CHAT_TURN_WORKER_CRASHED",
                "message": exception_detail["message"],
                "exception": exception_detail,
            }
        )
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
        if emit_status_events:
            emit_elf_event(
                ElfEventCreate(
                    source="chat",
                    mood="error",
                    motion="error",
                    message="这轮对话执行失败了，我记录了错误。",
                    priority=90,
                    ttl_ms=6000,
                    dedupe_key=f"chat:{turn_id}:failed",
                    metadata={"conversation_id": conversation_id, "turn_id": turn_id, "error": str(exc)},
                )
            )
        try:
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
                    error=exception_detail["message"],
                    debug_payload=debug_payload,
                )
        except Exception:
            logger.exception("failed to persist failure state for chat turn %s", turn_id)
        buffer.append(
            _sse(
                "error",
                {
                    "turn_id": turn_id,
                    "message": exception_detail["message"],
                    "exception": exception_detail,
                    "node_statuses": node_statuses,
                },
            )
        )
    finally:
        buffer.mark_done()


def _get_message_or_error(session: Session, message_id: int) -> ChatMessage:
    message = session.get(ChatMessage, message_id)
    if message is None:
        raise RuntimeError(f"ChatMessage {message_id} was not found after graph execution.")
    return message


def _normalize_chat_message_for_attachments(message: str, attachment_ids: list[int]) -> str:
    normalized = message.strip()
    if normalized:
        return normalized
    if attachment_ids:
        return "请分析我上传的附件。"
    raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Message or attachment is required")


def _sse(event: str, data: dict) -> str:
    """把事件编码为浏览器 EventSource/fetch 可读取的 SSE 文本。"""

    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _is_chat_turn_cancelled(session_factory: SessionFactory, turn_id: int) -> bool:
    try:
        with session_factory() as session:
            turn = session.get(ChatTurn, turn_id)
            return turn is not None and turn.status == "cancelled"
    except Exception:
        logger.exception("failed to check cancellation for chat turn %s", turn_id)
        return False


def _normalize_interrupt_payload(raw_interrupt) -> dict:
    interrupt = raw_interrupt if isinstance(raw_interrupt, dict) else {}
    value = interrupt.get("value") if isinstance(interrupt.get("value"), dict) else {}
    request = dict(value)
    request.setdefault("kind", "user_input")
    request.setdefault("request_id", interrupt.get("id") or "")
    request["interrupt_id"] = str(interrupt.get("id") or "")
    raw_questions = request.get("questions")
    if isinstance(raw_questions, list) and raw_questions:
        normalized_questions = []
        for index, item in enumerate(raw_questions):
            normalized_question = _normalize_user_input_question(item, index)
            if normalized_question:
                normalized_questions.append(normalized_question)
        request["questions"] = normalized_questions
        if normalized_questions:
            request["question"] = normalized_questions[0]["question"]
            request["options"] = normalized_questions[0]["options"]
            request["selection_mode"] = normalized_questions[0]["selection_mode"]
            request["allow_other"] = normalized_questions[0]["allow_other"]
            request["other_option"] = {
                "id": "other",
                "label": "其他",
                "value": "",
                "description": "自己输入一个答案。",
                "placeholder": normalized_questions[0]["other_placeholder"],
            }
        return request
    request["question"] = str(request.get("question") or "需要你补充一个选择。")
    selection_mode = str(request.get("selection_mode") or "single")
    request["selection_mode"] = selection_mode if selection_mode in {"single", "multiple"} else "single"
    raw_options = request.get("options")
    options = raw_options if isinstance(raw_options, list) else []
    normalized_options = []
    for index, option in enumerate(options):
        if not isinstance(option, dict):
            continue
        label = str(option.get("label") or option.get("value") or "").strip()
        value_text = str(option.get("value") or label).strip()
        if not label and not value_text:
            continue
        normalized_options.append(
            {
                "id": str(option.get("id") or f"option-{index + 1}"),
                "label": label or value_text,
                "value": value_text or label,
                "description": str(option.get("description") or "").strip(),
                "recommended": bool(option.get("recommended", index == 0)),
            }
        )
    request["options"] = normalized_options
    request["allow_other"] = bool(request.get("allow_other", True))
    other = request.get("other_option") if isinstance(request.get("other_option"), dict) else {}
    request["other_option"] = {
        "id": "other",
        "label": str(other.get("label") or "其他"),
        "value": "",
        "description": str(other.get("description") or "自己输入一个答案。"),
        "placeholder": str(other.get("placeholder") or "请输入其他答案"),
    }
    return request


def _normalize_user_input_question(raw_question, index: int) -> dict | None:
    if not isinstance(raw_question, dict):
        return None
    question = str(raw_question.get("question") or "").strip()
    raw_options = raw_question.get("options")
    options = raw_options if isinstance(raw_options, list) else []
    normalized_options = []
    for option_index, option in enumerate(options):
        if not isinstance(option, dict):
            continue
        label = str(option.get("label") or option.get("value") or "").strip()
        value_text = str(option.get("value") or label).strip()
        if not label and not value_text:
            continue
        normalized_options.append(
            {
                "id": str(option.get("id") or f"question-{index + 1}-option-{option_index + 1}"),
                "label": label or value_text,
                "value": value_text or label,
                "description": str(option.get("description") or "").strip(),
                "recommended": bool(option.get("recommended", option_index == 0)),
            }
        )
    if len(question) < 6 or len(normalized_options) < 2:
        return None
    selection_mode = str(raw_question.get("selection_mode") or "single")
    if selection_mode not in {"single", "multiple"}:
        selection_mode = "single"
    return {
        "id": str(raw_question.get("id") or f"question-{index + 1}"),
        "question": question,
        "options": normalized_options,
        "selection_mode": selection_mode,
        "allow_other": bool(raw_question.get("allow_other", True)),
        "other_placeholder": str(raw_question.get("other_placeholder") or "请输入其他答案"),
    }


def _decode_json_object(value: str) -> dict[str, str]:
    decoded = _decode_json_any(value, fallback={})
    return decoded if isinstance(decoded, dict) else {}


def _decode_json_any(value: str, *, fallback):
    try:
        return json.loads(value or "")
    except json.JSONDecodeError:
        return fallback


def _build_tool_invocation_payload(observation: dict, *, step_index: int) -> dict:
    """把 graph 内部的 AgentToolObservationPayload 整理成前端时间线用的 SSE 负载。

    只保留前端展示需要的字段（工具名、参数摘要、是否成功、结果摘要），避免把整段
    工具原始数据（可能很大）通过 SSE 推给浏览器。result_summary 复用 graph 节点
    里给 thought 用的同一份摘要函数，保持两边描述一致。
    """

    from app.agent.graphs.memory_chat.nodes import _summarize_tool_observation

    arguments = observation.get("arguments")
    if not isinstance(arguments, dict):
        arguments = {}
    running = bool(observation.get("running"))
    return {
        "step_index": int(step_index),
        "tool_call_id": str(observation.get("tool_call_id") or ""),
        "tool_name": str(observation.get("tool_name") or ""),
        "arguments": arguments,
        "ok": bool(observation.get("ok")),
        "blocked": bool(observation.get("blocked")),
        "error_code": str(observation.get("error_code") or ""),
        "message": str(observation.get("message") or ""),
        # running 卡片此时还没有摘要可言；让前端用 args 占位避免显示 "→ undefined"。
        "result_summary": "" if running else _summarize_tool_observation(observation),
        "running": running,
    }


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
        "context_l3_knowledge_layer",
        "context_l3_layer",
        "context_l2_layer",
        "context_l1_layer",
        "context_lx_attachment_layer",
        "context_lx_web_layer",
        "context_l0_adjacent_layer",
        "context_l0_layer",
        "context_conversation_window_layer",
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

    LangGraph updates 是节点完成事件；answer_delta 到达时 agent 处于 running。
    L3 的内部耗时来自 graph state.retrieval_debug，会在这里并入节点记录。

    ReAct 循环里同一节点（agent / tools）会被调用多次。为了让前端能分别查看
    每次调用后的 state 快照，这里在 invocations 列表里追加每次调用记录，
    顶层 status/started_ms/completed_ms 仍维持“最新一次”，保持向后兼容。
    """

    nodes = debug_payload.setdefault("nodes", {})
    node_payload = nodes.setdefault(node_name, {})
    invocations = node_payload.setdefault("invocations", [])
    now_ms = _elapsed_ms_since(started_at)

    if status == "running":
        # 找到一个尚未结束的 running 记录；如果没有就开一条新的。
        # ReAct 多轮调用时，每次新一轮 running 都应该开新条目。
        pending = next((entry for entry in invocations if entry.get("completed_ms") is None), None)
        if pending is None:
            pending = {
                "index": len(invocations),
                "status": "running",
                "started_ms": now_ms,
            }
            invocations.append(pending)
        else:
            pending.setdefault("started_ms", now_ms)
            pending["status"] = "running"
        current_entry = pending
        if "started_ms" not in node_payload:
            node_payload["started_ms"] = now_ms
    else:
        current_entry = next(
            (entry for entry in reversed(invocations) if entry.get("completed_ms") is None),
            None,
        )
        if current_entry is None:
            current_entry = {
                "index": len(invocations),
                "started_ms": now_ms,
            }
            invocations.append(current_entry)
        current_entry["status"] = status
        current_entry["completed_ms"] = now_ms
        started_ms_entry = current_entry.get("started_ms")
        if isinstance(started_ms_entry, int):
            current_entry["duration_ms"] = now_ms - started_ms_entry
        node_payload["completed_ms"] = now_ms
        started_ms = node_payload.get("started_ms")
        if isinstance(started_ms, int):
            node_payload["duration_ms"] = now_ms - started_ms

    node_payload["status"] = status
    node_payload["invocation_count"] = len(invocations)

    if state and node_name == "build_l3_retrieved_memory":
        retrieval_debug = state.get("retrieval_debug")
        if isinstance(retrieval_debug, dict):
            node_payload["retrieval_debug"] = retrieval_debug
    if state and node_name == "build_l3_knowledge_context":
        knowledge_debug = state.get("knowledge_retrieval_debug")
        if isinstance(knowledge_debug, dict):
            node_payload["knowledge_retrieval_debug"] = knowledge_debug
    if state:
        # 保存“节点完成后的累计 state 快照”，前端点击节点时可以直接查看。
        # 这里不直接暴露原始对象：一方面 state 可能包含较长文本，另一方面部分值
        # 不是 JSON 原生类型。调试快照会做长度裁剪，但保留关键字段结构。
        snapshot = _compact_debug_state(state)
        current_entry["state"] = snapshot
        node_payload["state"] = snapshot  # 兼容旧前端：保留最近一次


def _compact_debug_state(value, *, depth: int = 0):
    """把 LangGraph state 转成适合写入 ChatTurn.debug_payload 的调试快照。

    参数：
      value: 任意 graph state 值。
      depth: 当前递归深度，内部使用。

    返回：
      JSON 兼容对象。长字符串和长列表会被裁剪，避免一个 turn 的调试 payload
      因 prompt_context、chunk 内容或 turn_messages 过大而膨胀。
    """

    if depth >= 5:
        return _compact_debug_scalar(value)
    if isinstance(value, dict):
        return {str(key): _compact_debug_state(item, depth=depth + 1) for key, item in value.items()}
    if isinstance(value, list):
        limit = 20
        items = [_compact_debug_state(item, depth=depth + 1) for item in value[:limit]]
        if len(value) > limit:
            items.append({"__truncated__": len(value) - limit})
        return items
    return _compact_debug_scalar(value)


def _compact_debug_scalar(value):
    """规整调试快照中的标量值。"""

    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, str):
        max_length = 4000
        if len(value) > max_length:
            return f"{value[:max_length]}\n...[truncated {len(value) - max_length} chars]"
        return value
    return str(value)

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


def _build_exception_detail(exc: Exception) -> dict:
    """把异常整理成便于排查的结构化信息。

    turn.error 仍保留一行简短摘要；完整 traceback 写入 debug_payload，
    这样前端 graph 面板和后端日志都能看到真正的失败位置。
    """

    exc_type = type(exc).__name__
    message = str(exc).strip() or exc_type
    traceback_text = "".join(
        traceback.format_exception(type(exc), exc, exc.__traceback__)
    )
    return {
        "type": exc_type,
        "message": f"{exc_type}: {message}",
        "traceback": traceback_text,
    }


def _update_elf_runtime(
    session_factory: SessionFactory,
    *,
    status: str,
    conversation_id: int,
    turn_id: int,
    **kwargs,
) -> None:
    try:
        from app.services.elf_runtime_state_service import update_elf_runtime_state

        with session_factory() as session:
            update_elf_runtime_state(
                session,
                status=status,  # type: ignore[arg-type]
                conversation_id=conversation_id,
                turn_id=turn_id,
                **kwargs,
            )
    except Exception:
        logger.exception("failed to update elf runtime state for turn %s", turn_id)


def _create_streaming_message_pair(
    session: Session,
    *,
    conversation: Conversation,
    user_content: str,
    parent_message_id: int | None = None,
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
    parent_id = parent_message_id
    if parent_id is None:
        parent_id = _latest_message_id(session, conversation.id)
    else:
        parent = session.get(ChatMessage, parent_id)
        if parent is None or parent.conversation_id != conversation.id:
            raise ValueError("parent_message_id must reference a message in the same conversation.")
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
