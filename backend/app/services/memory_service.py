from fastapi import HTTPException, status
from sqlmodel import Session, desc, select

from app.models.long_term_memory import LongTermMemory
from app.models.note import utc_now
from app.rag.hashing import content_hash
from app.schemas.memory import MemoryRead, MemoryUpdate


ALLOWED_MEMORY_CATEGORIES = {
    "preference",
    "identity",
    "goal",
    "instruction",
    "event",
    "fact",
}
ALLOWED_MEMORY_STATUSES = {"active", "archived"}


def list_memories(
    session: Session,
    *,
    status_filter: str = "active",
    category: str | None = None,
    level: int = 4,
    limit: int = 50,
    offset: int = 0,
) -> list[MemoryRead]:
    """列出长期记忆。

    默认只返回 L4 active 记忆，因为这是 Memory Chat Graph 会放入 prompt 的范围。
    """

    _validate_status(status_filter)
    if category is not None:
        category = _normalize_category(category)
    limit = _normalize_limit(limit)
    offset = max(0, offset)

    statement = (
        select(LongTermMemory)
        .where(LongTermMemory.status == status_filter)
        .where(LongTermMemory.level == level)
        .order_by(
            desc(LongTermMemory.importance),
            desc(LongTermMemory.updated_at),
            desc(LongTermMemory.id),
        )
        .offset(offset)
        .limit(limit)
    )
    if category is not None:
        statement = statement.where(LongTermMemory.category == category)
    memories = session.exec(statement).all()
    return [_to_memory_read(memory) for memory in memories]


def get_memory(session: Session, memory_id: int) -> MemoryRead:
    """读取单条长期记忆。"""

    return _to_memory_read(_get_memory_or_404(session, memory_id))


def update_memory(
    session: Session,
    memory_id: int,
    payload: MemoryUpdate,
) -> MemoryRead:
    """更新长期记忆。

    修改 category 或 content 后会重新计算 content_hash，确保后续去重规则仍然有效。
    """

    if not payload.model_fields_set:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="At least one field is required.",
        )

    memory = _get_memory_or_404(session, memory_id)
    category_changed = False
    content_changed = False

    if "category" in payload.model_fields_set:
        if payload.category is None:
            raise _bad_request("category cannot be null.")
        memory.category = _normalize_category(payload.category)
        category_changed = True

    if "content" in payload.model_fields_set:
        if payload.content is None:
            raise _bad_request("content cannot be null.")
        memory.content = _normalize_content(payload.content)
        content_changed = True

    if "summary" in payload.model_fields_set:
        memory.summary = (payload.summary or "").strip()[:300]

    if "importance" in payload.model_fields_set:
        if payload.importance is None:
            raise _bad_request("importance cannot be null.")
        memory.importance = _normalize_score(payload.importance, "importance")

    if "confidence" in payload.model_fields_set:
        if payload.confidence is None:
            raise _bad_request("confidence cannot be null.")
        memory.confidence = _normalize_score(payload.confidence, "confidence")

    if "status" in payload.model_fields_set:
        if payload.status is None:
            raise _bad_request("status cannot be null.")
        memory.status = _validate_status(payload.status)

    if category_changed or content_changed:
        memory.content_hash = build_memory_content_hash(memory.category, memory.content)

    memory.updated_at = utc_now()
    session.add(memory)
    session.commit()
    session.refresh(memory)
    return _to_memory_read(memory)


def archive_memory(session: Session, memory_id: int) -> MemoryRead:
    """停用长期记忆。

    底层仍使用 archived 状态表示“不会进入 L4 上下文，但保留记录以便恢复”。
    """

    memory = _get_memory_or_404(session, memory_id)
    memory.status = "archived"
    memory.updated_at = utc_now()
    session.add(memory)
    session.commit()
    session.refresh(memory)
    return _to_memory_read(memory)


def build_memory_content_hash(category: str, content: str) -> str:
    """生成长期记忆去重 hash。

    与 conversation_memory_graph 的写入规则保持一致。
    """

    return content_hash(f"{category}:{content.strip().lower()}")


def _get_memory_or_404(session: Session, memory_id: int) -> LongTermMemory:
    memory = session.get(LongTermMemory, memory_id)
    if memory is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Memory not found",
        )
    return memory


def _normalize_category(category: str) -> str:
    normalized = category.strip().lower()
    if normalized not in ALLOWED_MEMORY_CATEGORIES:
        raise _bad_request("Invalid memory category.")
    return normalized


def _validate_status(memory_status: str) -> str:
    normalized = memory_status.strip().lower()
    if normalized not in ALLOWED_MEMORY_STATUSES:
        raise _bad_request("Invalid memory status.")
    return normalized


def _normalize_content(content: str) -> str:
    normalized = content.strip()
    if not normalized:
        raise _bad_request("content cannot be empty.")
    return normalized[:1000]


def _normalize_score(value: float, field_name: str) -> float:
    if value < 0.0 or value > 1.0:
        raise _bad_request(f"{field_name} must be between 0.0 and 1.0.")
    return float(value)


def _normalize_limit(limit: int) -> int:
    return max(1, min(limit, 200))


def _bad_request(detail: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


def _to_memory_read(memory: LongTermMemory) -> MemoryRead:
    return MemoryRead(
        id=memory.id or 0,
        level=memory.level,
        category=memory.category,
        content=memory.content,
        summary=memory.summary,
        importance=memory.importance,
        confidence=memory.confidence,
        source_type=memory.source_type,
        source_id=memory.source_id,
        status=memory.status,
        content_hash=memory.content_hash,
        created_at=memory.created_at,
        updated_at=memory.updated_at,
    )
