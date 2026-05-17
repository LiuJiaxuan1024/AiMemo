from fastapi import APIRouter, Depends, Query, Response, status
from sqlmodel import Session

from app.core.database import get_session
from app.schemas.memory import MemoryRead, MemoryUpdate
from app.services.memory_service import (
    archive_memory,
    delete_archived_memory,
    get_memory,
    list_memories,
    update_memory,
)


router = APIRouter(prefix="/memories", tags=["memories"])


@router.get("", response_model=list[MemoryRead])
def list_memories_api(
    status: str = Query(default="active"),
    category: str | None = Query(default=None),
    level: int = Query(default=4),
    limit: int = Query(default=50),
    offset: int = Query(default=0),
    session: Session = Depends(get_session),
) -> list[MemoryRead]:
    return list_memories(
        session,
        status_filter=status,
        category=category,
        level=level,
        limit=limit,
        offset=offset,
    )


@router.get("/{memory_id}", response_model=MemoryRead)
def get_memory_api(
    memory_id: int,
    session: Session = Depends(get_session),
) -> MemoryRead:
    return get_memory(session, memory_id)


@router.patch("/{memory_id}", response_model=MemoryRead)
def update_memory_api(
    memory_id: int,
    payload: MemoryUpdate,
    session: Session = Depends(get_session),
) -> MemoryRead:
    return update_memory(session, memory_id, payload)


@router.delete("/{memory_id}", response_model=MemoryRead)
def archive_memory_api(
    memory_id: int,
    session: Session = Depends(get_session),
) -> MemoryRead:
    return archive_memory(session, memory_id)


@router.delete("/{memory_id}/hard", status_code=status.HTTP_204_NO_CONTENT)
def delete_archived_memory_api(
    memory_id: int,
    session: Session = Depends(get_session),
) -> Response:
    delete_archived_memory(session, memory_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
