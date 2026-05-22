from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlmodel import Session

from app.core.database import get_session
from app.schemas.chat import ChatRequest, ChatResponse, ChatTurnGraphRead, ChatTurnStateHistoryRead
from app.services.chat_service import run_conversation_chat, stream_conversation_chat_events
from app.services.chat_turn_service import (
    get_chat_turn_graph_by_message,
    get_chat_turn_graph_by_turn,
    get_chat_turn_state_history,
)


router = APIRouter(prefix="/conversations", tags=["chat"])


@router.post("/{conversation_id}/chat", response_model=ChatResponse)
def run_conversation_chat_api(
    conversation_id: int,
    payload: ChatRequest,
) -> ChatResponse:
    # 该接口是 memory_chat_graph 的 HTTP 入口：保存用户消息、生成回答、保存 AI 消息。
    return run_conversation_chat(conversation_id, message=payload.message)


@router.post("/{conversation_id}/chat/stream")
def stream_conversation_chat_api(
    conversation_id: int,
    payload: ChatRequest,
) -> StreamingResponse:
    # SSE 入口：前端通过它获得 graph 节点进度、回答增量和最终消息落库结果。
    return StreamingResponse(
        stream_conversation_chat_events(conversation_id, message=payload.message),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get(
    "/{conversation_id}/messages/{message_id}/graph",
    response_model=ChatTurnGraphRead,
)
def get_message_graph_api(
    conversation_id: int,
    message_id: int,
    session: Session = Depends(get_session),
) -> ChatTurnGraphRead:
    # 通过 assistant message 反查本轮 graph，供聊天窗口里的调试按钮使用。
    return get_chat_turn_graph_by_message(
        session,
        conversation_id=conversation_id,
        assistant_message_id=message_id,
    )


@router.get(
    "/{conversation_id}/turns/{turn_id}/graph",
    response_model=ChatTurnGraphRead,
)
def get_turn_graph_api(
    conversation_id: int,
    turn_id: int,
    session: Session = Depends(get_session),
) -> ChatTurnGraphRead:
    # 通过 ChatTurn 直接读取 graph，支持 assistant 消息尚未完成时查看运行状态。
    return get_chat_turn_graph_by_turn(
        session,
        conversation_id=conversation_id,
        turn_id=turn_id,
    )


@router.get(
    "/{conversation_id}/turns/{turn_id}/state-history",
    response_model=ChatTurnStateHistoryRead,
)
def get_turn_state_history_api(
    conversation_id: int,
    turn_id: int,
    session: Session = Depends(get_session),
) -> ChatTurnStateHistoryRead:
    # 读取 LangGraph 原生 checkpoint state history，供 Graph 调试面板做时间线查看。
    return get_chat_turn_state_history(
        session,
        conversation_id=conversation_id,
        turn_id=turn_id,
    )
