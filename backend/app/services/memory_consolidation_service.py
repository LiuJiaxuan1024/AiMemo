"""长期记忆归并服务。

conversation_memory_graph 抽取出的记忆只是“候选”。本服务负责在写入前判断
这些候选是否和已有 L4 记忆重复、是否应该更新已有记忆，或是否应该创建新记忆。
"""

from collections.abc import Callable
from dataclasses import asdict, dataclass
from difflib import SequenceMatcher
from typing import Any
import re

from langchain_core.messages import HumanMessage, SystemMessage
from sqlmodel import Session, desc, select

from app.ai.json_utils import parse_json_object
from app.agent.model import get_planner_chat_model
from app.models.long_term_memory import LongTermMemory
from app.services.memory_service import build_memory_content_hash


ConsolidationJudge = Callable[
    ["NormalizedMemoryCandidate", list[LongTermMemory]],
    "ConsolidationDecision",
]

HIGH_SIMILARITY_SKIP_THRESHOLD = 0.88
MAX_CANDIDATE_MEMORIES = 8


@dataclass(frozen=True)
class NormalizedMemoryCandidate:
    """准备写入长期记忆的标准化候选。"""

    category: str
    content: str
    summary: str
    importance: float
    confidence: float


@dataclass(frozen=True)
class ConsolidationDecision:
    """归并决策。

    action:
      - skip: 不写入。
      - create: 创建新长期记忆。
      - update: 更新 existing_memory_id 指向的已有记忆。
    """

    action: str
    category: str
    content: str
    summary: str
    importance: float
    confidence: float
    reason: str
    existing_memory_id: int | None = None


def consolidate_memory_candidates(
    session: Session,
    candidates: list[NormalizedMemoryCandidate],
    *,
    judge: ConsolidationJudge | None = None,
) -> list[dict[str, Any]]:
    """对候选长期记忆进行归并判断。

    返回 dict 是为了能直接进入 LangGraph checkpoint，避免 dataclass 在持久化时
    引入额外序列化约定。
    """

    decisions: list[ConsolidationDecision] = []
    for candidate in candidates:
        exact_duplicate = _find_exact_duplicate(session, candidate)
        if exact_duplicate is not None:
            decisions.append(
                _skip_decision(
                    candidate,
                    reason="content_hash 已存在，跳过精确重复记忆。",
                    existing_memory_id=exact_duplicate.id,
                )
            )
            continue

        similar_memories = find_candidate_memories(session, candidate)
        obvious_duplicate = _find_obvious_duplicate(candidate, similar_memories)
        if obvious_duplicate is not None:
            decisions.append(
                _skip_decision(
                    candidate,
                    reason="与已有长期记忆高度相似，判定为重复记忆。",
                    existing_memory_id=obvious_duplicate.id,
                )
            )
            continue

        # 中文短句在字面相似度上可能偏低，但同 category 的长期记忆数量通常不大。
        # 明显重复先用本地规则跳过，其余同类候选交给 LLM 判断，避免漏掉语义重复。
        if similar_memories:
            decisions.append(
                _coerce_decision(
                    (judge or judge_memory_consolidation)(candidate, similar_memories),
                    candidate,
                )
            )
            continue

        decisions.append(
            ConsolidationDecision(
                action="create",
                category=candidate.category,
                content=candidate.content,
                summary=candidate.summary,
                importance=candidate.importance,
                confidence=candidate.confidence,
                reason="未发现相似 active L4 记忆，创建新记忆。",
            )
        )

    return [asdict(decision) for decision in decisions]


def _coerce_decision(
    value: ConsolidationDecision | dict[str, Any],
    candidate: NormalizedMemoryCandidate,
) -> ConsolidationDecision:
    """兼容测试和未来服务扩展中直接返回 dict 的 judge。"""

    if isinstance(value, ConsolidationDecision):
        return value
    action = str(value.get("action") or "create").strip().lower()
    if action not in {"skip", "create", "update"}:
        action = "create"
    existing_memory_id = value.get("existing_memory_id")
    try:
        existing_memory_id = int(existing_memory_id) if existing_memory_id is not None else None
    except (TypeError, ValueError):
        existing_memory_id = None
    return ConsolidationDecision(
        action=action,
        existing_memory_id=existing_memory_id,
        category=str(value.get("category") or candidate.category),
        content=str(value.get("content") or candidate.content).strip()[:1000],
        summary=str(value.get("summary") or candidate.summary).strip()[:300],
        importance=_clamp_float(value.get("importance", candidate.importance)),
        confidence=_clamp_float(value.get("confidence", candidate.confidence)),
        reason=str(value.get("reason") or "外部 judge 归并判断。").strip()[:500],
    )


def find_candidate_memories(
    session: Session,
    candidate: NormalizedMemoryCandidate,
) -> list[LongTermMemory]:
    """召回同 category 的 active L4 候选记忆。

    第一版不引入 memory embedding，先用同类记忆的 importance / updated_at 排序召回。
    后续可以替换为 sqlite-vec top_k 检索。
    """

    statement = (
        select(LongTermMemory)
        .where(LongTermMemory.level == 4)
        .where(LongTermMemory.status == "active")
        .where(LongTermMemory.category == candidate.category)
        .order_by(desc(LongTermMemory.importance), desc(LongTermMemory.updated_at))
        .limit(MAX_CANDIDATE_MEMORIES)
    )
    return list(session.exec(statement).all())


def judge_memory_consolidation(
    candidate: NormalizedMemoryCandidate,
    existing_memories: list[LongTermMemory],
) -> ConsolidationDecision:
    """使用轻量 planner 模型判断候选记忆与已有记忆的关系。"""

    existing_text = "\n".join(
        (
            f"{index + 1}. id={memory.id}\n"
            f"   category: {memory.category}\n"
            f"   content: {memory.content}\n"
            f"   summary: {memory.summary}\n"
            f"   importance: {memory.importance}\n"
            f"   confidence: {memory.confidence}"
        )
        for index, memory in enumerate(existing_memories)
    )
    prompt = (
        "你是 Ai 记的长期记忆归并器。判断新候选记忆是否和已有长期记忆重复，"
        "或是否应该更新已有记忆。只返回严格 JSON，不要输出 markdown。\n\n"
        "可选 action：skip, create, update。\n"
        "判断原则：\n"
        "- 同一主体、同一目标、同一事实，只是措辞不同 -> skip。\n"
        "- 新内容更准确、更完整，且仍是同一事实 -> update。\n"
        "- 表达不同事实 -> create。\n"
        "- 不要因为中文近义词差异创建新记忆。\n\n"
        "输出 JSON 格式：\n"
        "{"
        "\"action\":\"skip|create|update\","
        "\"existing_memory_id\":18,"
        "\"content\":\"归并后的中文长期记忆\","
        "\"summary\":\"短摘要\","
        "\"importance\":0.9,"
        "\"confidence\":1.0,"
        "\"reason\":\"简短原因\""
        "}\n\n"
        f"新候选记忆：\n"
        f"category: {candidate.category}\n"
        f"content: {candidate.content}\n"
        f"summary: {candidate.summary}\n"
        f"importance: {candidate.importance}\n"
        f"confidence: {candidate.confidence}\n\n"
        f"已有记忆候选：\n{existing_text}"
    )
    model = get_planner_chat_model().bind(response_format={"type": "json_object"})
    response = model.invoke(
        [
            SystemMessage(content="你负责为个人知识库做长期记忆去重和归并。"),
            HumanMessage(content=prompt),
        ]
    )
    return _parse_judge_response(str(response.content), candidate, existing_memories)


def _parse_judge_response(
    text: str,
    candidate: NormalizedMemoryCandidate,
    existing_memories: list[LongTermMemory],
) -> ConsolidationDecision:
    try:
        payload = parse_json_object(text)
    except Exception:  # noqa: BLE001 - 归并失败时宁可创建，也不要丢失候选记忆。
        return ConsolidationDecision(
            action="create",
            category=candidate.category,
            content=candidate.content,
            summary=candidate.summary,
            importance=candidate.importance,
            confidence=candidate.confidence,
            reason="归并判断 JSON 解析失败，降级为创建新记忆。",
        )

    action = str(payload.get("action") or "create").strip().lower()
    if action not in {"skip", "create", "update"}:
        action = "create"

    existing_ids = {memory.id for memory in existing_memories if memory.id is not None}
    existing_memory_id = payload.get("existing_memory_id")
    try:
        existing_memory_id = int(existing_memory_id) if existing_memory_id is not None else None
    except (TypeError, ValueError):
        existing_memory_id = None
    if existing_memory_id not in existing_ids:
        existing_memory_id = None
    if action in {"skip", "update"} and existing_memory_id is None:
        action = "create"

    return ConsolidationDecision(
        action=action,
        existing_memory_id=existing_memory_id,
        category=candidate.category,
        content=str(payload.get("content") or candidate.content).strip()[:1000],
        summary=str(payload.get("summary") or candidate.summary).strip()[:300],
        importance=_clamp_float(payload.get("importance", candidate.importance)),
        confidence=_clamp_float(payload.get("confidence", candidate.confidence)),
        reason=str(payload.get("reason") or "LLM 归并判断。").strip()[:500],
    )


def _find_exact_duplicate(
    session: Session,
    candidate: NormalizedMemoryCandidate,
) -> LongTermMemory | None:
    memory_hash = build_memory_content_hash(candidate.category, candidate.content)
    return session.exec(
        select(LongTermMemory)
        .where(LongTermMemory.content_hash == memory_hash)
        .where(LongTermMemory.status == "active")
    ).first()


def _find_obvious_duplicate(
    candidate: NormalizedMemoryCandidate,
    existing_memories: list[LongTermMemory],
) -> LongTermMemory | None:
    for memory in existing_memories:
        if _memory_similarity(candidate.content, memory.content) >= HIGH_SIMILARITY_SKIP_THRESHOLD:
            return memory
    return None


def _skip_decision(
    candidate: NormalizedMemoryCandidate,
    *,
    reason: str,
    existing_memory_id: int | None,
) -> ConsolidationDecision:
    return ConsolidationDecision(
        action="skip",
        existing_memory_id=existing_memory_id,
        category=candidate.category,
        content=candidate.content,
        summary=candidate.summary,
        importance=candidate.importance,
        confidence=candidate.confidence,
        reason=reason,
    )


def _memory_similarity(left: str, right: str) -> float:
    left_normalized = _normalize_similarity_text(left)
    right_normalized = _normalize_similarity_text(right)
    if not left_normalized or not right_normalized:
        return 0.0
    return SequenceMatcher(None, left_normalized, right_normalized).ratio()


def _normalize_similarity_text(value: str) -> str:
    normalized = value.strip().lower()
    normalized = re.sub(r"[，。！？、；：,.!?;:\s'\"“”‘’（）()《》<>【】\[\]-]", "", normalized)
    # 去掉对长期事实影响很小、但容易造成措辞差异的状态词。
    for token in ("正在", "打算", "计划中", "目前", "现在"):
        normalized = normalized.replace(token, "")
    return normalized


def _clamp_float(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return 0.0
    return max(0.0, min(1.0, number))
