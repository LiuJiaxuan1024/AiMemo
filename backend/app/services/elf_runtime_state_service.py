from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
import json
from typing import Any

from sqlmodel import Session

from app.core.database import session_scope
from app.models.chat_turn import ChatTurn
from app.models.elf_runtime_state import ElfRuntimeState
from app.models.note import utc_now
from app.schemas.elf import ElfRuntimeStateRead, ElfRuntimeStatus
from app.services.chat_turn_service import _pending_interrupt_from_turn


SessionFactory = Callable[[], AbstractContextManager[Session]]

ELF_RUNTIME_STATE_ID = 1
BUSY_STATUSES = {
    "thinking",
    "tool_running",
    "streaming_answer",
    "speaking",
    "waiting_user_input",
    "recovering",
}


class _Keep:
    pass


_KEEP = _Keep()


def get_elf_runtime_state(
    *,
    session_factory: SessionFactory = session_scope,
) -> ElfRuntimeStateRead:
    with session_factory() as session:
        state = get_or_create_elf_runtime_state(session)
        state = reconcile_elf_runtime_state(session, state)
        return to_elf_runtime_state_read(state)


def get_or_create_elf_runtime_state(session: Session) -> ElfRuntimeState:
    state = session.get(ElfRuntimeState, ELF_RUNTIME_STATE_ID)
    if state is not None:
        return state
    state = ElfRuntimeState(id=ELF_RUNTIME_STATE_ID)
    session.add(state)
    session.commit()
    session.refresh(state)
    return state


def update_elf_runtime_state(
    session: Session,
    *,
    status: ElfRuntimeStatus,
    conversation_id: int | None | object = _KEEP,
    turn_id: int | None | object = _KEEP,
    pending_interrupt: dict[str, Any] | None | object = _KEEP,
    last_message: str | object = _KEEP,
    last_bubbles: list[dict[str, Any]] | object = _KEEP,
    last_error: str | object = _KEEP,
) -> ElfRuntimeState:
    state = get_or_create_elf_runtime_state(session)
    state.status = status
    if conversation_id is not _KEEP:
        state.conversation_id = conversation_id  # type: ignore[assignment]
    if turn_id is not _KEEP:
        state.turn_id = turn_id  # type: ignore[assignment]
    if pending_interrupt is not _KEEP:
        state.pending_interrupt = _json_dumps(pending_interrupt or {})
    if last_message is not _KEEP:
        state.last_message = str(last_message)
    if last_bubbles is not _KEEP:
        state.last_bubbles = _json_dumps(last_bubbles if isinstance(last_bubbles, list) else [])
    if last_error is not _KEEP:
        state.last_error = str(last_error)
    state.updated_at = utc_now()
    session.add(state)
    session.commit()
    session.refresh(state)
    return state


def mark_elf_runtime_idle(session: Session) -> ElfRuntimeState:
    return update_elf_runtime_state(
        session,
        status="idle",
        conversation_id=None,
        turn_id=None,
        pending_interrupt={},
        last_error="",
    )


def reconcile_elf_runtime_state(session: Session, state: ElfRuntimeState | None = None) -> ElfRuntimeState:
    """修正刷新/重启后遗留的精灵运行时状态。

    真正可恢复的等待选择必须同时满足：
    - runtime state 指向一个具体 turn；
    - 该 turn 仍是 interrupted；
    - turn 里还保留合法 pending_interrupt。

    任何一边缺失都说明这是旧状态，不能继续禁用精灵输入。
    """

    state = state or get_or_create_elf_runtime_state(session)
    if state.status != "waiting_user_input":
        return state

    turn = session.get(ChatTurn, state.turn_id) if state.turn_id is not None else None
    pending_interrupt = _pending_interrupt_from_turn(turn) if turn is not None else None
    if (
        turn is not None
        and turn.status == "interrupted"
        and turn.conversation_id == state.conversation_id
        and _has_valid_pending_interrupt(pending_interrupt)
    ):
        if not _has_valid_pending_interrupt(_json_object(state.pending_interrupt)):
            return update_elf_runtime_state(
                session,
                status="waiting_user_input",
                pending_interrupt=pending_interrupt,
                last_error="",
            )
        return state

    return update_elf_runtime_state(
        session,
        status="failed",
        conversation_id=None,
        turn_id=None,
        pending_interrupt={},
        last_error="刚才那轮选择已失效，重新说一遍就好。",
    )


def to_elf_runtime_state_read(state: ElfRuntimeState) -> ElfRuntimeStateRead:
    status = _normalize_status(state.status)
    pending_interrupt = _json_object(state.pending_interrupt)
    last_bubbles = _json_list(state.last_bubbles)
    return ElfRuntimeStateRead(
        status=status,
        busy=status in BUSY_STATUSES,
        conversation_id=state.conversation_id,
        turn_id=state.turn_id,
        pending_interrupt=pending_interrupt or None,
        last_message=state.last_message,
        last_bubbles=last_bubbles,
        last_error=state.last_error,
        message=_status_message(status, state),
        updated_at=state.updated_at,
    )


def _normalize_status(value: str) -> ElfRuntimeStatus:
    allowed = {
        "idle",
        "thinking",
        "tool_running",
        "streaming_answer",
        "speaking",
        "waiting_user_input",
        "completed",
        "failed",
        "recovering",
    }
    return value if value in allowed else "idle"  # type: ignore[return-value]


def _status_message(status: ElfRuntimeStatus, state: ElfRuntimeState) -> str:
    if status == "waiting_user_input":
        return "刚才我停在一个选择上，继续选一下我就能接着做。"
    if status == "thinking":
        return "精灵正在整理上下文。"
    if status == "tool_running":
        return "精灵正在执行工具。"
    if status == "streaming_answer":
        return "精灵正在组织回复。"
    if status == "speaking":
        return state.last_message or "精灵正在说话。"
    if status == "failed":
        return state.last_error or "刚才那轮处理被中断了，重新说一遍就好。"
    return ""


def _has_valid_pending_interrupt(pending_interrupt: dict[str, Any] | None) -> bool:
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


def _json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def _json_object(value: str) -> dict[str, Any]:
    try:
        payload = json.loads(value or "{}")
        return payload if isinstance(payload, dict) else {}
    except json.JSONDecodeError:
        return {}


def _json_list(value: str) -> list[dict[str, Any]]:
    try:
        payload = json.loads(value or "[]")
        return [item for item in payload if isinstance(item, dict)] if isinstance(payload, list) else []
    except json.JSONDecodeError:
        return []
