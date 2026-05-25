from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from sqlmodel import Session

from app.core.database import get_session
from app.schemas.chat import (
    ChatActiveTurnListRead,
    ChatRequest,
    ChatResumeRequest,
    ChatResponse,
    ChatTurnGraphRead,
    ChatTurnStateHistoryRead,
)
from app.services.chat_service import (
    run_conversation_chat,
    stream_conversation_chat_events,
    stream_conversation_chat_resume_events,
    stream_existing_turn_events,
)
from app.services.chat_turn_service import (
    get_chat_turn_graph_by_message,
    get_chat_turn_graph_by_turn,
    get_chat_turn_state_history,
    list_active_chat_turns,
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


@router.get(
    "/{conversation_id}/active-turns",
    response_model=ChatActiveTurnListRead,
)
def list_active_turns_api(
    conversation_id: int,
    session: Session = Depends(get_session),
) -> ChatActiveTurnListRead:
    # 用户切走再回到该会话时调用：拿到目前还在跑的 turn 列表，
    # 用其中的 turn_id 订阅 /turns/{turn_id}/events/stream 把后续 SSE 接回来。
    return list_active_chat_turns(session, conversation_id=conversation_id)


@router.get("/{conversation_id}/turns/{turn_id}/events/stream")
def stream_existing_turn_events_api(
    conversation_id: int,
    turn_id: int,
) -> StreamingResponse:
    # SSE 重连入口：在 chat_turn_buffer 还在 retention 窗口内时，把历史事件
    # 从 index 0 重放，再跟着 live 拿后续事件。graph 已经在后台线程里跑，
    # 不会被这次 GET 的连接生命周期影响。
    # 这里没有强校验 conversation_id 与 turn 的归属——active-turns 列表已经做过
    # 该过滤；保留 conversation_id 在 URL 是为了与其他 turn 接口语义一致。
    return StreamingResponse(
        stream_existing_turn_events(turn_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.post("/{conversation_id}/turns/{turn_id}/resume/stream")
def resume_interrupted_turn_api(
    conversation_id: int,
    turn_id: int,
    payload: ChatResumeRequest,
) -> StreamingResponse:
    return StreamingResponse(
        stream_conversation_chat_resume_events(
            conversation_id,
            turn_id,
            resume_payload=payload.model_dump(mode="json"),
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
