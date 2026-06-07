from collections.abc import Callable
from contextlib import AbstractContextManager
from threading import Lock

from sqlmodel import Session, desc, select

from app.core.database import session_scope
from app.models.conversation import Conversation
from app.schemas.conversation import ConversationCreate
from app.schemas.chat import ChatActiveTurnRead
from app.services.chat_service import stream_conversation_chat_events
from app.services import chat_turn_buffer
from app.services.chat_turn_service import fail_chat_turn, list_active_chat_turns, recover_stale_chat_turns
from app.services.conversation_service import create_conversation
from app.services.elf_runtime_state_service import get_or_create_elf_runtime_state, update_elf_runtime_state


SessionFactory = Callable[[], AbstractContextManager[Session]]

ELF_CONVERSATION_TITLE = "Memo Elf"
_elf_chat_run_lock = Lock()


def get_or_create_elf_conversation(
    *,
    session_factory: SessionFactory = session_scope,
) -> Conversation:
    """读取或创建桌面精灵专用对话。

    外置精灵聊天和 AiMemo 内置聊天复用同一套 Memory Chat Graph，但业务入口需要一条
    稳定的 conversation，用来保存上下文、checkpoint thread 和后续对话状态树。
    """

    with session_factory() as session:
        conversation = session.exec(
            select(Conversation)
            .where(Conversation.title == ELF_CONVERSATION_TITLE, Conversation.status == "active")
            .order_by(desc(Conversation.updated_at), desc(Conversation.id))
        ).first()
        if conversation is not None:
            return conversation

        created = create_conversation(session, ConversationCreate(title=ELF_CONVERSATION_TITLE))
        conversation = session.get(Conversation, created.id)
        if conversation is None:
            raise RuntimeError("Elf conversation was not created.")
        return conversation


def stream_elf_chat_events(
    *,
    message: str,
    session_factory: SessionFactory = session_scope,
    checkpoint_path: str | None = None,
):
    """桌面精灵聊天 SSE。

    它内部仍执行 memory_chat_graph，但关闭精灵状态播报。消费端只关心回答文本，
    不需要看到“开始检索/开始写入记忆”等工作型气泡。
    """

    conversation = get_or_create_elf_conversation(session_factory=session_factory)
    conversation_id = conversation.id
    if conversation_id is None:
        raise RuntimeError("Elf conversation id is required.")

    with session_factory() as session:
        active_turns = _list_elf_active_turns(session, conversation_id=conversation_id)
    if active_turns:
        yield 'event: error\ndata: {"message":"精灵上一轮还没有结束，请先完成当前回复或选择。","code":"ELF_CHAT_BUSY"}\n\n'
        return

    acquired = _elf_chat_run_lock.acquire(blocking=False)
    if not acquired:
        yield 'event: error\ndata: {"message":"精灵上一轮回复仍在后台处理中，请等当前回复完成后再发送。","code":"ELF_CHAT_BUSY"}\n\n'
        return

    try:
        yield from stream_conversation_chat_events(
            conversation_id,
            message=message,
            session_factory=session_factory,
            checkpoint_path=checkpoint_path,
            emit_status_events=False,
            answer_mode="elf_bubble",
            runtime_scope="elf",
        )
    finally:
        _elf_chat_run_lock.release()


def stream_elf_chat_resume_events(
    *,
    turn_id: int,
    resume_payload: dict,
    session_factory: SessionFactory = session_scope,
    checkpoint_path: str | None = None,
):
    conversation = get_or_create_elf_conversation(session_factory=session_factory)
    conversation_id = conversation.id
    if conversation_id is None:
        raise RuntimeError("Elf conversation id is required.")

    from app.services.chat_service import stream_conversation_chat_resume_events

    acquired = _elf_chat_run_lock.acquire(blocking=False)
    if not acquired:
        yield 'event: error\ndata: {"message":"精灵正在处理上一条请求，请稍后再试。","code":"ELF_CHAT_BUSY"}\n\n'
        return

    try:
        yield from stream_conversation_chat_resume_events(
            conversation_id,
            turn_id,
            resume_payload=resume_payload,
            session_factory=session_factory,
            checkpoint_path=checkpoint_path,
            emit_status_events=False,
            answer_mode="elf_bubble",
            runtime_scope="elf",
        )
    finally:
        _elf_chat_run_lock.release()


def get_elf_chat_status(
    *,
    session_factory: SessionFactory = session_scope,
) -> dict:
    conversation = get_or_create_elf_conversation(session_factory=session_factory)
    conversation_id = conversation.id
    if conversation_id is None:
        raise RuntimeError("Elf conversation id is required.")

    lock_busy = _elf_chat_run_lock.locked()
    with session_factory() as session:
        active_turns = _list_elf_active_turns(
            session,
            conversation_id=conversation_id,
            lock_busy=lock_busy,
        )

    active_turn = active_turns[-1] if active_turns else None
    status = active_turn.status if active_turn else ("running" if lock_busy else "idle")
    return {
        "busy": bool(lock_busy or active_turn),
        "status": status,
        "turn_id": active_turn.turn_id if active_turn else None,
        "message": _elf_chat_status_message(status),
    }


def _list_elf_active_turns(
    session: Session,
    *,
    conversation_id: int,
    lock_busy: bool = False,
) -> list[ChatActiveTurnRead]:
    """读取精灵活跃 turn，并清理后端刷新/重启留下的孤儿 running 状态。

    普通聊天可以等通用 stale timeout 慢慢收敛；桌面精灵的全局 busy 状态会直接禁用输入，
    所以只要 DB 显示 running、但当前进程里已经没有对应的 live buffer，就应当立即
    判定为孤儿 turn，而不是让用户一直看到"后台处理中"。
    """

    active_turns = list_active_chat_turns(session, conversation_id=conversation_id).items
    stale_running_turn_ids: list[int] = []
    stale_interrupted_turns: list[ChatActiveTurnRead] = []
    for turn in active_turns:
        if turn.status == "interrupted":
            if not _is_current_elf_interrupted_turn(session, conversation_id=conversation_id, turn=turn):
                stale_interrupted_turns.append(turn)
            continue
        if turn.status != "running":
            continue
        buffer = chat_turn_buffer.get(turn.turn_id)
        if lock_busy or (buffer is not None and not buffer.done):
            continue
        stale_running_turn_ids.append(turn.turn_id)

    if not stale_running_turn_ids and not stale_interrupted_turns:
        return active_turns

    for turn_id in stale_running_turn_ids:
        recover_stale_chat_turns(
            session,
            conversation_id=conversation_id,
            turn_id=turn_id,
            timeout_seconds=0,
        )

    for turn in stale_interrupted_turns:
        node_statuses = dict(turn.node_statuses)
        for node_name, node_status in list(node_statuses.items()):
            if node_status == "interrupted":
                node_statuses[node_name] = "failed"
            elif node_status == "pending":
                node_statuses[node_name] = "skipped"
        fail_chat_turn(
            session,
            turn.turn_id,
            node_statuses=node_statuses,
            error="精灵上一轮等待选择已失效，已自动释放。",
        )

    active_turns = list_active_chat_turns(session, conversation_id=conversation_id).items
    if not active_turns:
        recovered_turn_id = (
            stale_interrupted_turns[-1].turn_id
            if stale_interrupted_turns
            else stale_running_turn_ids[-1]
        )
        update_elf_runtime_state(
            session,
            status="failed",
            conversation_id=conversation_id,
            turn_id=recovered_turn_id,
            pending_interrupt={},
            last_error="刚才那轮处理被中断了，重新说一遍就好。",
        )
    return active_turns


def _is_current_elf_interrupted_turn(
    session: Session,
    *,
    conversation_id: int,
    turn: ChatActiveTurnRead,
) -> bool:
    pending_interrupt = turn.pending_interrupt if isinstance(turn.pending_interrupt, dict) else None
    if not _has_valid_pending_interrupt(pending_interrupt):
        return False

    runtime_state = get_or_create_elf_runtime_state(session)
    return (
        runtime_state.status == "waiting_user_input"
        and runtime_state.conversation_id == conversation_id
        and runtime_state.turn_id == turn.turn_id
    )


def _has_valid_pending_interrupt(pending_interrupt: dict | None) -> bool:
    if not isinstance(pending_interrupt, dict) or not pending_interrupt:
        return False
    question = str(pending_interrupt.get("question") or "").strip()
    questions = pending_interrupt.get("questions")
    options = pending_interrupt.get("options")
    return bool(
        question
        or (isinstance(questions, list) and questions)
        or (isinstance(options, list) and options)
    )


def _elf_chat_status_message(status: str) -> str:
    if status == "running":
        return "精灵上一轮回复仍在后台处理中。"
    if status == "interrupted":
        return "精灵上一轮回复正在等你选择，请先完成选择后再继续。"
    return ""
