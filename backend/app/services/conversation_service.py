from __future__ import annotations

import json
import logging

from fastapi import HTTPException, status
from sqlmodel import Session, desc, select

from app.models.agent_operation import AgentOperation
from app.models.background_task import BackgroundTask
from app.models.chat_message import ChatMessage
from app.models.chat_turn import ChatTurn
from app.models.conversation import Conversation
from app.models.job import Job
from app.models.long_term_memory import LongTermMemory
from app.models.note import utc_now
from app.rag.chunking.tokenizer import count_tokens
from app.schemas.conversation import (
    ChatMessageCreate,
    ChatMessageRead,
    ConversationCreate,
    ConversationListItem,
    ConversationRead,
)

logger = logging.getLogger(__name__)


def create_conversation(session: Session, payload: ConversationCreate) -> ConversationRead:
    """创建一个业务对话线程。

    参数：
      session: 当前数据库会话。
      payload: 创建请求，当前只包含可选标题。

    返回：
      ConversationRead。创建后会立即写入 langgraph_thread_id，格式为 conversation:{id}。
    """

    title = payload.title.strip() or "新对话"
    conversation = Conversation(title=title)
    session.add(conversation)
    session.flush()
    if conversation.id is None:
        raise RuntimeError("Conversation id was not generated.")
    conversation.langgraph_thread_id = f"conversation:{conversation.id}"
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return _to_conversation_read(conversation)


def list_conversations(session: Session) -> list[ConversationListItem]:
    """按更新时间倒序返回对话列表。"""

    conversations = session.exec(select(Conversation).order_by(desc(Conversation.updated_at))).all()
    return [_to_conversation_list_item(conversation) for conversation in conversations]


def get_conversation(session: Session, conversation_id: int) -> ConversationRead:
    """读取单个对话，不存在时返回 404。"""

    return _to_conversation_read(_get_conversation_or_404(session, conversation_id))


def list_messages(session: Session, conversation_id: int) -> list[ChatMessageRead]:
    """读取某个对话的消息。

    MVP 阶段按创建顺序展示线性消息；后续做状态树 UI 时，可以按 parent_id 组装树。
    """

    _get_conversation_or_404(session, conversation_id)
    messages = session.exec(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at, ChatMessage.id)
    ).all()
    turn_info_by_assistant_message_id = _load_turn_info_by_assistant_message_id(
        session,
        conversation_id=conversation_id,
    )
    return [
        _to_chat_message_read(
            message,
            turn_id=(turn_info_by_assistant_message_id.get(message.id or {}) or {}).get("turn_id"),
            pending_interrupt=(turn_info_by_assistant_message_id.get(message.id or {}) or {}).get("pending_interrupt"),
        )
        for message in messages
    ]


def append_message(
    session: Session,
    conversation_id: int,
    payload: ChatMessageCreate,
) -> ChatMessageRead:
    """向对话追加一条消息。

    参数：
      session: 当前数据库会话。
      conversation_id: 消息所属对话。
      payload: 消息内容、角色、可选 parent_id/checkpoint_id。

    行为：
      - 如果 parent_id 为空，默认接在当前会话最后一条消息后。
      - 如果 parent_id 不为空，会验证父消息属于同一个 conversation。
      - token_count 使用本地 tokenizer 粗略计算，后续用于上下文预算。
    """

    conversation = _get_conversation_or_404(session, conversation_id)
    parent_id = payload.parent_id
    if parent_id is None:
        parent_id = _latest_message_id(session, conversation_id)
    else:
        _ensure_parent_message_belongs_to_conversation(session, conversation_id, parent_id)

    message = ChatMessage(
        conversation_id=conversation_id,
        role=payload.role,
        content=payload.content,
        parent_id=parent_id,
        checkpoint_id=payload.checkpoint_id,
        status=payload.status,
        token_count=count_tokens(payload.content),
    )
    session.add(message)
    conversation.updated_at = utc_now()
    session.add(conversation)
    session.commit()
    session.refresh(message)
    return _to_chat_message_read(message)


def delete_message_branch(
    session: Session,
    conversation_id: int,
    message_id: int,
) -> None:
    """删除某条消息所在 turn 及其后续依赖消息。

    线性聊天里后续消息会把上一条消息作为 parent；如果只删中间一轮，
    后面的回答仍然携带已删除上下文的影响。这里按 parent 链删除分支，
    保证 UI 和后续上下文一致。
    """

    conversation = _get_conversation_or_404(session, conversation_id)
    message = session.get(ChatMessage, message_id)
    if message is None or message.conversation_id != conversation_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")

    root_message_id = _resolve_turn_root_message_id(session, conversation_id, message)
    delete_ids = _collect_descendant_message_ids(session, conversation_id, root_message_id)

    active_turns = session.exec(
        select(ChatTurn).where(
            ChatTurn.conversation_id == conversation_id,
            ChatTurn.status.in_(["running", "interrupted"]),
        )
    ).all()
    for turn in active_turns:
        if turn.user_message_id in delete_ids or turn.assistant_message_id in delete_ids:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="Cannot delete a running or interrupted turn",
            )

    if delete_ids:
        memories = session.exec(
            select(LongTermMemory).where(
                LongTermMemory.source_type == "chat_message",
                LongTermMemory.source_id.in_(delete_ids),
            )
        ).all()
        for memory in memories:
            session.delete(memory)

        turns = session.exec(
            select(ChatTurn).where(ChatTurn.conversation_id == conversation_id)
        ).all()
        for turn in turns:
            if turn.user_message_id in delete_ids or turn.assistant_message_id in delete_ids:
                session.delete(turn)

        messages = session.exec(
            select(ChatMessage).where(
                ChatMessage.conversation_id == conversation_id,
                ChatMessage.id.in_(delete_ids),
            )
        ).all()
        for item in messages:
            session.delete(item)

    if conversation.summary_message_id is not None and conversation.summary_message_id in delete_ids:
        conversation.summary = ""
        conversation.summary_message_id = None
    conversation.updated_at = utc_now()
    session.add(conversation)
    session.commit()


def delete_conversation(session: Session, conversation_id: int) -> None:
    """级联删除一个对话及其所有相关资源。

    清理顺序（外到内，先停活动资源、再删数据库行、最后删 checkpoint）：
      1. 杀掉 / 移除该对话的所有后台命令任务（含 OS 进程探活清理）。
      2. 删除挂在该对话消息上的 LongTermMemory（source_type=chat_message）。
      3. 删除 AgentOperation 审计记录。
      4. 删除与该对话相关的 Job（按 dedupe_key 前缀匹配）。
      5. 删除 ChatTurn / ChatMessage。
      6. 删除 LangGraph SqliteSaver checkpoint（thread_id=conversation:{id}）。
      7. 删除 Conversation 本体。

    所有 best-effort 清理（pool kill、checkpoint 删除）都用 try/except 包裹，
    单个步骤失败不会阻塞主流程；保证至少把数据库主表删干净。
    """

    conversation = _get_conversation_or_404(session, conversation_id)

    _cleanup_background_tasks(session, conversation_id)

    chat_message_ids = [
        message.id
        for message in session.exec(
            select(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
        ).all()
        if message.id is not None
    ]
    if chat_message_ids:
        memories = session.exec(
            select(LongTermMemory).where(
                LongTermMemory.source_type == "chat_message",
                LongTermMemory.source_id.in_(chat_message_ids),
            )
        ).all()
        for memory in memories:
            session.delete(memory)

    operations = session.exec(
        select(AgentOperation).where(AgentOperation.conversation_id == conversation_id)
    ).all()
    for op in operations:
        session.delete(op)

    job_prefix = f"conversation_%:conversation:{conversation_id}"
    jobs = session.exec(
        select(Job).where(Job.dedupe_key.like(job_prefix))
    ).all()
    for job in jobs:
        session.delete(job)

    turns = session.exec(
        select(ChatTurn).where(ChatTurn.conversation_id == conversation_id)
    ).all()
    for turn in turns:
        session.delete(turn)

    messages = session.exec(
        select(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
    ).all()
    for message in messages:
        session.delete(message)

    _delete_langgraph_checkpoint(conversation_id)

    session.delete(conversation)
    session.commit()


def _cleanup_background_tasks(session: Session, conversation_id: int) -> None:
    """杀掉并移除某个对话的所有后台命令任务。

    先调用 pool.kill 终止 OS 进程并把内存池中的任务下线，再调用 pool.prune
    清掉日志文件与生产 DB 行；最后无条件 session.delete 本会话 session 中的
    BackgroundTask 行，确保即便 pool 操作失败也不留孤儿记录。
    """

    records = session.exec(
        select(BackgroundTask).where(BackgroundTask.conversation_id == conversation_id)
    ).all()
    if not records:
        return

    try:
        from app.local_operator.background_command import pool
    except Exception:
        logger.exception("无法加载 BackgroundShellPool，仅删除数据库行")
        pool = None  # type: ignore[assignment]

    for record in records:
        task_id = record.task_id
        if pool is not None:
            try:
                pool.kill(task_id, reason=f"conversation {conversation_id} deleted")
            except Exception:
                logger.exception("kill background task %s 失败", task_id)
            try:
                pool.prune(task_id)
            except Exception:
                logger.exception("prune background task %s 失败", task_id)
        session.delete(record)


def _delete_langgraph_checkpoint(conversation_id: int) -> None:
    """尽力删除该对话在 LangGraph SqliteSaver 中的 checkpoint。"""

    thread_id = f"conversation:{conversation_id}"
    try:
        from app.agent.checkpoints import get_sqlite_checkpointer

        with get_sqlite_checkpointer() as checkpointer:
            delete_method = getattr(checkpointer, "delete_thread", None)
            if callable(delete_method):
                delete_method(thread_id)
                return
    except Exception:
        logger.exception("通过 SqliteSaver 删除 checkpoint 失败，回退到原始 SQL")

    try:
        import sqlite3
        from app.core.config import settings

        with sqlite3.connect(settings.langgraph_checkpoint_path) as conn:
            for table in ("checkpoints", "writes", "checkpoint_blobs", "checkpoint_writes"):
                try:
                    conn.execute(f"DELETE FROM {table} WHERE thread_id = ?", (thread_id,))
                except sqlite3.OperationalError:
                    continue
            conn.commit()
    except Exception:
        logger.exception("原始 SQL 删除 checkpoint 也失败，已跳过（不影响主流程）")


def _get_conversation_or_404(session: Session, conversation_id: int) -> Conversation:
    conversation = session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    return conversation


def _latest_message_id(session: Session, conversation_id: int) -> int | None:
    message = session.exec(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(desc(ChatMessage.created_at), desc(ChatMessage.id))
    ).first()
    return message.id if message else None


def _resolve_turn_root_message_id(
    session: Session,
    conversation_id: int,
    message: ChatMessage,
) -> int:
    turn = session.exec(
        select(ChatTurn).where(
            ChatTurn.conversation_id == conversation_id,
            (ChatTurn.user_message_id == message.id)
            | (ChatTurn.assistant_message_id == message.id),
        )
    ).first()
    if turn and turn.user_message_id:
        return int(turn.user_message_id)
    if message.role == "assistant" and message.parent_id is not None:
        parent = session.get(ChatMessage, message.parent_id)
        if parent is not None and parent.conversation_id == conversation_id and parent.role == "user":
            return int(parent.id or message.id or 0)
    if message.id is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Message not found")
    return int(message.id)


def _collect_descendant_message_ids(
    session: Session,
    conversation_id: int,
    root_message_id: int,
) -> set[int]:
    messages = session.exec(
        select(ChatMessage).where(ChatMessage.conversation_id == conversation_id)
    ).all()
    children_by_parent: dict[int, list[int]] = {}
    for message in messages:
        if message.id is None or message.parent_id is None:
            continue
        children_by_parent.setdefault(message.parent_id, []).append(message.id)

    result: set[int] = set()
    stack = [root_message_id]
    while stack:
        current = stack.pop()
        if current in result:
            continue
        result.add(current)
        stack.extend(children_by_parent.get(current, []))
    return result


def _ensure_parent_message_belongs_to_conversation(
    session: Session,
    conversation_id: int,
    parent_id: int,
) -> None:
    parent = session.get(ChatMessage, parent_id)
    if parent is None or parent.conversation_id != conversation_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="parent_id must reference a message in the same conversation",
        )


def _to_conversation_read(conversation: Conversation) -> ConversationRead:
    return ConversationRead(
        id=conversation.id or 0,
        title=conversation.title,
        status=conversation.status,
        summary=conversation.summary,
        summary_message_id=conversation.summary_message_id,
        langgraph_thread_id=conversation.langgraph_thread_id,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def _to_conversation_list_item(conversation: Conversation) -> ConversationListItem:
    return ConversationListItem(
        id=conversation.id or 0,
        title=conversation.title,
        status=conversation.status,
        summary=conversation.summary,
        summary_message_id=conversation.summary_message_id,
        langgraph_thread_id=conversation.langgraph_thread_id,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def _load_turn_info_by_assistant_message_id(
    session: Session,
    *,
    conversation_id: int,
) -> dict[int, dict]:
    """读取 assistant message 与 ChatTurn 的映射。

    Graph 调试视图是以 `ChatTurn` 为事实来源的。前端只有拿到明确的 turn_id，
    才应该显示 graph 按钮；否则历史消息、摘要消息或外部入口消息会误触发 404。
    """

    turns = session.exec(
        select(ChatTurn).where(
            ChatTurn.conversation_id == conversation_id,
            ChatTurn.assistant_message_id.is_not(None),
        )
    ).all()
    result: dict[int, dict] = {}
    for turn in turns:
        if turn.id is None or turn.assistant_message_id is None:
            continue
        pending_interrupt = None
        if turn.status == "interrupted":
            pending_interrupt = _pending_interrupt_from_turn(turn)
        result[int(turn.assistant_message_id)] = {
            "turn_id": int(turn.id),
            "pending_interrupt": pending_interrupt,
        }
    return result


def _pending_interrupt_from_turn(turn: ChatTurn) -> dict | None:
    try:
        payload = json.loads(turn.debug_payload or "{}")
    except Exception:
        return None
    pending = payload.get("pending_interrupt") if isinstance(payload, dict) else None
    return pending if isinstance(pending, dict) else None


def _to_chat_message_read(
    message: ChatMessage,
    *,
    turn_id: int | None = None,
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
