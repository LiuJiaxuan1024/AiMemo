from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse

from app.schemas.chat import ChatRequest
from app.schemas.chat import ChatResumeRequest
from app.schemas.elf import ElfEventCreate, ElfEventListRead, ElfEventRead, ElfRuntimeStateRead
from app.services.elf_chat_service import get_elf_chat_status, stream_elf_chat_events, stream_elf_chat_resume_events
from app.services.elf_event_service import elf_event_service
from app.services.elf_runtime_state_service import get_elf_runtime_state


router = APIRouter(prefix="/elf", tags=["elf"])


@router.post("/events", response_model=ElfEventRead | None)
def publish_elf_event_api(payload: ElfEventCreate) -> ElfEventRead | None:
    """调试/扩展入口：允许外部模块向精灵事件中心发布事件。

    生产路径优先由后端业务代码直接调用 service，这个 HTTP 入口主要方便手动调试
    或未来让桌面端把用户点击精灵的行为回传给后端。
    """

    return elf_event_service.publish(payload)


@router.get("/events", response_model=ElfEventListRead)
def list_elf_events_api(
    after_id: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
) -> ElfEventListRead:
    """读取精灵事件流，桌面精灵和浏览器可以并行消费。"""

    events = elf_event_service.list_after(after_id, limit=limit)
    return ElfEventListRead(events=events, latest_id=elf_event_service.latest_id())


@router.post("/chat/stream")
def stream_elf_chat_api(payload: ChatRequest) -> StreamingResponse:
    """桌面精灵外置聊天入口。

    该接口封装 conversation_id 和 graph 调试细节，桌面端只需要发送用户输入并消费
    answer_delta/done/error。内部仍走 Memory Chat Graph，因此上下文、检索和记忆能力
    与 AiMemo 内置聊天保持一致。
    """

    return StreamingResponse(
        stream_elf_chat_events(message=payload.message),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@router.get("/chat/status")
def get_elf_chat_status_api() -> dict:
    return get_elf_chat_status()


@router.get("/runtime/status", response_model=ElfRuntimeStateRead)
def get_elf_runtime_status_api() -> ElfRuntimeStateRead:
    return get_elf_runtime_state()


@router.post("/chat/turns/{turn_id}/resume/stream")
def resume_elf_chat_api(turn_id: int, payload: ChatResumeRequest) -> StreamingResponse:
    return StreamingResponse(
        stream_elf_chat_resume_events(turn_id=turn_id, resume_payload=payload.model_dump(mode="json")),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
