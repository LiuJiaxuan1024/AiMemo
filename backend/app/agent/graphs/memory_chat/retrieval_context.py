from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from contextlib import AbstractContextManager
from dataclasses import dataclass
import logging
import re
from typing import Literal

from langchain_core.messages import HumanMessage
from sqlmodel import Session

from app.ai.json_utils import parse_json_object
from app.agent.context import ContextBudget, build_retrieved_memory_layer
from app.agent.graphs.memory_chat.state import ChatMessagePayload, MemoryChatGraphState, RetrievedChunkPayload
from app.agent.model import get_planner_chat_model
from app.core.config import settings
from app.core.timing import elapsed_ms, emit_timing, now_counter
from app.rag.search import NoteSearchResult, search_notes, search_notes_keyword


SessionFactory = Callable[[], AbstractContextManager[Session]]
logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class RetrievalPlan:
    """检索计划结果。

    plan 节点不只判断“要不要检索”，还负责给出检索 query。
    这样后续可以把多源检索、多 query 检索、worker 并行都挂在 plan 输出之后。
    """

    intent: str
    needs_retrieval: bool
    needs_query_rewrite: bool
    retrieval_query: str
    confidence: float
    reason: str
    source: str = "unknown"


@dataclass(frozen=True)
class NoteRetrievalDecision:
    """个人笔记 L3 的轻量检索决策。"""

    action: Literal["skip", "light", "vector"]
    query: str
    confidence: float
    reason: str
    source: str = "rule"



RetrievalPlanner = Callable[[str, list[ChatMessagePayload]], RetrievalPlan]
NoteRetriever = Callable[..., list[NoteSearchResult]]


def _context_budget() -> ContextBudget:
    return settings.context_pyramid_budget


def _resolve_user_message(state: MemoryChatGraphState) -> str:
    user_message = state.get("user_message", "").strip()
    if not user_message:
        raise ValueError("user_message is required.")
    return user_message


def build_l3_retrieved_memory_node(
    session_factory: SessionFactory,
    *,
    planner: RetrievalPlanner | None = None,
    retriever: NoteRetriever = search_notes,
    limit: int = 5,
):
    """构建 L3 个人笔记检索层。

    默认每轮执行 cheap recall；只有明确个人记忆意图或可选 planner 要求时才升级向量检索。
    这样保留个人 Agent 的高召回倾向，同时避免每轮 embedding/vector 检索拖慢对话。
    """

    def build_l3_retrieved_memory(state: MemoryChatGraphState) -> MemoryChatGraphState:
        user_message = _resolve_user_message(state)
        total_started_at = now_counter()
        failed_stage = ""
        planner_elapsed_ms = 0
        cheap_recall_elapsed_ms = 0
        retriever_elapsed_ms = 0
        grade_elapsed_ms = 0
        layer_elapsed_ms = 0
        plan = RetrievalPlan(
            intent="rag",
            needs_retrieval=False,
            needs_query_rewrite=False,
            retrieval_query=user_message,
            confidence=1.0,
            reason="个人笔记默认执行轻量关键词召回，必要时升级向量检索。",
            source="cheap_note_recall",
        )
        decision = NoteRetrievalDecision(
            action="light",
            query=user_message,
            confidence=0.6,
            reason="默认执行轻量关键词召回。",
        )
        retrieved_chunks: list[RetrievedChunkPayload] = []
        retrieval_grade: Literal["good", "weak", "poor", "none"] = "none"
        retrieval_grade_reason = "尚未完成个人笔记检索。"
        retrieval_query = user_message

        try:
            if planner is not None:
                failed_stage = "planner"
                planner_started_at = now_counter()
                rewritten_plan = planner(user_message, state.get("recent_messages", []))
                planner_elapsed_ms = elapsed_ms(planner_started_at)
                plan = RetrievalPlan(
                    intent="rag",
                    needs_retrieval=rewritten_plan.needs_retrieval,
                    needs_query_rewrite=rewritten_plan.needs_query_rewrite,
                    retrieval_query=rewritten_plan.retrieval_query or user_message,
                    confidence=rewritten_plan.confidence,
                    reason=(
                        f"{rewritten_plan.reason}；个人笔记默认先执行 cheap recall，"
                        "planner 只用于 query rewrite 或显式升级向量检索。"
                    ),
                    source=rewritten_plan.source,
                )

            retrieval_query = plan.retrieval_query or user_message
            with session_factory() as session:
                failed_stage = "cheap_recall"
                cheap_recall_started_at = now_counter()
                cheap_results = search_notes_keyword(session, query=retrieval_query, limit=limit)
                cheap_recall_elapsed_ms = elapsed_ms(cheap_recall_started_at)
                decision = _decide_note_retrieval(
                    user_message=user_message,
                    retrieval_query=retrieval_query,
                    cheap_results=cheap_results,
                    plan=plan if planner is not None else None,
                )
                if decision.action == "vector":
                    failed_stage = "retriever"
                    retriever_started_at = now_counter()
                    results = retriever(session, query=decision.query, limit=limit)
                    retriever_elapsed_ms = elapsed_ms(retriever_started_at)
                elif decision.action == "light":
                    results = cheap_results
                else:
                    results = []

            retrieval_query = decision.query
            retrieved_chunks = [_to_retrieved_chunk_payload(result) for result in results]
            failed_stage = "grade"
            grade_started_at = now_counter()
            retrieval_grade, retrieval_grade_reason = _grade_retrieval_chunks(retrieved_chunks)
            grade_elapsed_ms = elapsed_ms(grade_started_at)
            needs_retrieval = decision.action != "skip"

            failed_stage = "layer"
            layer_started_at = now_counter()
            layer = build_retrieved_memory_layer(
                retrieved_chunks,
                needs_retrieval,
                retrieval_grade,
                _context_budget(),
            )
            layer_elapsed_ms = elapsed_ms(layer_started_at)
            retrieval_debug = {
                "planner_ms": planner_elapsed_ms,
                "cheap_recall_ms": cheap_recall_elapsed_ms,
                "retriever_ms": retriever_elapsed_ms,
                "grade_ms": grade_elapsed_ms,
                "layer_ms": layer_elapsed_ms,
                "total_ms": elapsed_ms(total_started_at),
                "planner_source": plan.source,
                "retrieval_action": decision.action,
                "decision_source": decision.source,
                "decision_confidence": decision.confidence,
                "decision_reason": decision.reason,
                "needs_retrieval": needs_retrieval,
                "retrieval_query": retrieval_query,
                "retrieved_count": len(retrieved_chunks),
            }
        except Exception as exc:
            # L3 是增强上下文，不应因为 embedding/API/检索链路波动阻断主对话。
            layer_started_at = now_counter()
            layer = build_retrieved_memory_layer([], decision.action != "skip", "none", _context_budget())
            layer_elapsed_ms = elapsed_ms(layer_started_at)
            retrieval_debug = {
                "planner_ms": planner_elapsed_ms,
                "cheap_recall_ms": cheap_recall_elapsed_ms,
                "retriever_ms": retriever_elapsed_ms,
                "grade_ms": grade_elapsed_ms,
                "layer_ms": layer_elapsed_ms,
                "total_ms": elapsed_ms(total_started_at),
                "planner_source": plan.source,
                "retrieval_action": decision.action,
                "decision_source": decision.source,
                "decision_confidence": decision.confidence,
                "decision_reason": decision.reason,
                "needs_retrieval": decision.action != "skip",
                "retrieval_query": retrieval_query,
                "retrieved_count": 0,
                "degraded": True,
                "failed_stage": failed_stage or "unknown",
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
            plan = RetrievalPlan(
                intent="rag",
                needs_retrieval=decision.action != "skip",
                needs_query_rewrite=False,
                retrieval_query=retrieval_query,
                confidence=0.0,
                reason="L3 检索失败，已降级为直接回答。",
                source="fallback",
            )
            retrieved_chunks = []
            retrieval_grade = "none"
            retrieval_grade_reason = "L3 检索失败，已降级为直接回答。"
            emit_timing("memory_chat.l3_failed", **retrieval_debug)
            logger.exception("memory_chat.l3_failed %s", retrieval_debug)

        logger.info("memory_chat.l3_timing %s", retrieval_debug)
        return {
            "intent": plan.intent,
            "needs_retrieval": bool(retrieval_debug.get("needs_retrieval", plan.needs_retrieval)),
            "needs_query_rewrite": plan.needs_query_rewrite,
            "retrieval_query": retrieval_query,
            "plan_confidence": plan.confidence,
            "retrieval_reason": plan.reason,
            "retrieved_chunks": retrieved_chunks,
            "retrieval_grade": retrieval_grade,
            "retrieval_grade_reason": retrieval_grade_reason,
            "retrieval_debug": retrieval_debug,
            "context_l3_layer": layer.to_payload(),
        }

    return build_l3_retrieved_memory


def default_retrieval_planner(
    user_message: str,
    recent_messages: list[ChatMessagePayload],
) -> RetrievalPlan:
    """默认检索规划器：规则快路径 + LLM 兜底。

    规则明确时不调用额外 LLM；规则不确定时，使用 qwen-turbo 结构化判断，
    并允许模型给出改写后的 retrieval_query。
    """

    rule_result = _rule_plan_retrieval(user_message)
    if rule_result != "uncertain":
        return rule_result
    return _llm_plan_retrieval(user_message, recent_messages)


def _rule_plan_retrieval(user_message: str) -> RetrievalPlan | Literal["uncertain"]:
    normalized = user_message.strip()
    profile_keywords = [
        "我是一个怎么样的人",
        "我是个怎么样的人",
        "我是怎样的人",
        "我是个怎样的人",
        "我是一个什么样的人",
        "我是个什么样的人",
        "你觉得我是",
        "评价一下我",
        "评价我",
        "我的性格",
        "我的特点",
        "我的画像",
        "你了解我",
    ]
    if any(keyword in normalized for keyword in profile_keywords):
        return RetrievalPlan(
            intent="rag",
            needs_retrieval=True,
            needs_query_rewrite=True,
            retrieval_query="用户个人画像 性格特质 生活偏好 近期计划 行为记录",
            confidence=0.92,
            reason="规则判断为个人画像类问题，直接检索用户记忆。",
            source="rule_profile",
        )

    must_retrieve_keywords = [
        "我之前",
        "之前我",
        "上次",
        "以前",
        "记得",
        "我说过",
        "笔记",
        "提到过",
        "有没有",
        "来着",
        "啥来着",
        "什么来着",
        "那个",
        "那件事",
        "那个地方",
        "那个东西",
    ]
    if any(keyword in normalized for keyword in must_retrieve_keywords):
        return RetrievalPlan(
            intent="rag",
            needs_retrieval=True,
            needs_query_rewrite=False,
            retrieval_query=normalized,
            confidence=0.9,
            reason="用户问题包含个人记忆查询线索。",
            source="rule_memory_keyword",
        )

    direct_patterns = ["1+1", "等于几", "天气怎么样", "你好", "hello", "hi"]
    if any(pattern in normalized.lower() for pattern in direct_patterns):
        return RetrievalPlan(
            intent="direct",
            needs_retrieval=False,
            needs_query_rewrite=False,
            retrieval_query="",
            confidence=0.85,
            reason="规则判断为普通问题，不需要查询个人知识库。",
            source="rule_direct",
        )

    return "uncertain"


def _llm_plan_retrieval(
    user_message: str,
    recent_messages: list[ChatMessagePayload],
) -> RetrievalPlan:
    total_started_at = now_counter()
    recent_text = "\n".join(
        f"{message['role']}: {message['content']}" for message in recent_messages[-6:]
    ) or "无"
    prompt_started_at = now_counter()
    prompt = (
        "你是 Ai 记的检索规划器。判断用户问题是否需要查询用户的个人笔记/记忆库，"
        "并在需要时给出适合向量检索的中文 query。\n\n"
        "只返回 JSON，不要输出其他文本。JSON 格式：\n"
        "{"
        "\"intent\":\"direct 或 rag\","
        "\"needs_retrieval\":true,"
        "\"needs_query_rewrite\":false,"
        "\"retrieval_query\":\"用于检索的 query\","
        "\"confidence\":0.0,"
        "\"reason\":\"简短原因\""
        "}\n\n"
        "判断原则：\n"
        "- 如果用户询问自己的过去记录、偏好、计划、笔记内容，需要检索。\n"
        "- 如果是常识、数学、普通闲聊，不需要检索。\n"
        "- 如果用户使用“那个/刚刚/来着”等指代词，结合近期对话改写 query。\n\n"
        f"近期对话：\n{recent_text}\n\n"
        f"用户问题：{user_message}"
    )
    prompt_ms = elapsed_ms(prompt_started_at)
    try:
        model_started_at = now_counter()
        model = get_planner_chat_model()
        model_factory_ms = elapsed_ms(model_started_at)
        invoke_started_at = now_counter()
        response = model.invoke([HumanMessage(content=prompt)])
        invoke_ms = elapsed_ms(invoke_started_at)
        parse_started_at = now_counter()
        payload = parse_json_object(str(response.content))
        parse_ms = elapsed_ms(parse_started_at)
        needs_retrieval = bool(payload.get("needs_retrieval", False))
        retrieval_query = str(payload.get("retrieval_query") or user_message).strip()
        emit_timing(
            "memory_chat.planner_llm_timing",
            total_ms=elapsed_ms(total_started_at),
            prompt_ms=prompt_ms,
            model_factory_ms=model_factory_ms,
            invoke_ms=invoke_ms,
            parse_ms=parse_ms,
            prompt_chars=len(prompt),
            recent_count=len(recent_messages),
            response_chars=len(str(response.content)),
            model=getattr(model, "model_name", ""),
            needs_retrieval=needs_retrieval,
            needs_query_rewrite=bool(payload.get("needs_query_rewrite", False)),
        )
        return RetrievalPlan(
            intent="rag" if needs_retrieval else "direct",
            needs_retrieval=needs_retrieval,
            needs_query_rewrite=bool(payload.get("needs_query_rewrite", False)),
            retrieval_query=retrieval_query if needs_retrieval else "",
            confidence=float(payload.get("confidence", 0.5)),
            reason=str(payload.get("reason") or "LLM 检索规划结果。"),
            source="llm",
        )
    except Exception as exc:
        # 规划失败时走保守策略：不让异常打断聊天，但把含糊问题交给直接回答。
        # 后续可以把该错误写入观测日志。
        emit_timing(
            "memory_chat.planner_llm_timing",
            total_ms=elapsed_ms(total_started_at),
            prompt_ms=locals().get("prompt_ms", 0),
            model_factory_ms=locals().get("model_factory_ms", 0),
            invoke_ms=locals().get("invoke_ms", 0),
            parse_ms=locals().get("parse_ms", 0),
            prompt_chars=len(prompt) if "prompt" in locals() else 0,
            recent_count=len(recent_messages),
            error=repr(exc),
        )
        return RetrievalPlan(
            intent="direct",
            needs_retrieval=False,
            needs_query_rewrite=False,
            retrieval_query="",
            confidence=0.2,
            reason=f"检索规划失败，降级为直接回答：{exc}",
            source="llm_failed",
        )


def _direct_retrieval_plan(reason: str) -> RetrievalPlan:
    return RetrievalPlan(
        intent="direct",
        needs_retrieval=False,
        needs_query_rewrite=False,
        retrieval_query="",
        confidence=0.85,
        reason=reason,
        source="rule_direct",
    )


def _decide_note_retrieval(
    *,
    user_message: str,
    retrieval_query: str,
    cheap_results: list[NoteSearchResult],
    plan: RetrievalPlan | None,
) -> NoteRetrievalDecision:
    """个人笔记检索门控。

    问题不是“需不需要检索”，而是“能不能安全跳过重检索”。
    每轮 cheap recall 已经执行；这里只决定是否跳过、使用 cheap 结果，或升级向量检索。
    """

    normalized = user_message.strip().lower()
    if _is_safe_skip_note_retrieval(normalized):
        return NoteRetrievalDecision(
            action="skip",
            query="",
            confidence=0.95,
            reason="明确闲聊、纯算术或纯格式转换，可以安全跳过个人笔记检索。",
            source="rule_safe_skip",
        )

    if plan is not None and plan.needs_retrieval:
        return NoteRetrievalDecision(
            action="vector",
            query=plan.retrieval_query or retrieval_query,
            confidence=max(plan.confidence, 0.75),
            reason="注入 planner 明确要求个人笔记向量检索。",
            source=plan.source,
        )

    if _has_explicit_personal_memory_intent(normalized):
        return NoteRetrievalDecision(
            action="vector",
            query=retrieval_query,
            confidence=0.9,
            reason="用户明确询问个人记忆、笔记、历史记录或个人画像，需要向量检索。",
            source="rule_explicit_memory",
        )

    if cheap_results:
        return NoteRetrievalDecision(
            action="light",
            query=retrieval_query,
            confidence=0.8,
            reason="轻量关键词召回已有候选，先把候选交给 agent 判断。",
            source="cheap_recall_hit",
        )

    return NoteRetrievalDecision(
        action="light",
        query=retrieval_query,
        confidence=0.55,
        reason="未发现明确个人记忆意图，且轻量召回无候选；不升级向量检索以避免每轮阻塞。",
        source="cheap_recall_miss",
    )


def _is_safe_skip_note_retrieval(normalized_message: str) -> bool:
    compact = re.sub(r"\s+", "", normalized_message)
    if not compact:
        return True
    casual_messages = {
        "你好",
        "您好",
        "hello",
        "hi",
        "hey",
        "晚上好",
        "早上好",
        "下午好",
        "在吗",
        "谢谢",
        "感谢",
        "ok",
        "好的",
    }
    if compact in casual_messages:
        return True
    if re.fullmatch(r"\d+([+\-*/x×÷]\d+)+([=＝]|等于)?(多少|几|是什么|呢|吗)?[\?？]?", compact):
        return True
    if re.fullmatch(r"(把|将).{1,60}(翻译成|译成|改成)(英文|中文|日文|韩文|英语|汉语)", compact):
        return True
    if re.fullmatch(r"(python|js|javascript|java|c\+\+)?怎么打印(hello|helloworld|hello world)", compact):
        return True
    return False


def _has_explicit_personal_memory_intent(normalized_message: str) -> bool:
    triggers = (
        "我之前",
        "我以前",
        "我上次",
        "我说过",
        "我提到",
        "我记录",
        "我写过",
        "我的笔记",
        "笔记里",
        "记录过",
        "记得我",
        "你记得",
        "还记得",
        "上次说",
        "之前说",
        "以前说",
        "来着",
        "我的项目",
        "我的计划",
        "我的偏好",
        "我的性格",
        "评价我",
        "了解我",
        "个人画像",
        "长期记忆",
    )
    return any(trigger in normalized_message for trigger in triggers)



def _grade_retrieval_chunks(
    chunks: list[RetrievedChunkPayload],
    *,
    good_threshold: float = 0.5,
    weak_threshold: float = 0.42,
) -> tuple[Literal["good", "weak", "poor", "none"], str]:
    """轻量评估检索质量。

    L3 worker 内部使用这套规则，后续如果升级为 L3 子图也应复用同一阈值。
    """

    if not chunks:
        return "none", "没有检索到候选记忆。"

    top_score = max(float(chunk["score"]) for chunk in chunks)
    if top_score >= good_threshold:
        return "good", f"最高相似度分数 {top_score:.3f} 达到 good 阈值。"
    if top_score >= weak_threshold:
        return "weak", f"最高相似度分数 {top_score:.3f} 仅达到 weak 阈值。"
    return "poor", f"最高相似度分数 {top_score:.3f} 低于可用阈值。"


def _to_retrieved_chunk_payload(result: NoteSearchResult) -> RetrievedChunkPayload:
    return {
        "note_id": result.note_id,
        "note_title": result.note_title,
        "chunk_id": result.chunk_id,
        "chunk_index": result.chunk_index,
        "content": result.content,
        "content_hash": result.content_hash,
        "token_count": result.token_count,
        "distance": result.distance,
        "score": result.score,
    }
