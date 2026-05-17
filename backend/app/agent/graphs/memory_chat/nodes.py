from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
import logging
from typing import Literal

from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.types import Send
from sqlmodel import Session, desc, select

from app.ai.json_utils import parse_json_object
from app.agent.context import (
    ContextBudget,
    PyramidPromptContext,
    build_core_memory_layer,
    build_current_input_layer,
    build_recent_messages_layer,
    build_retrieved_memory_layer,
    build_summary_layer,
    context_layer_from_payload,
)
from app.agent.graphs.memory_chat.state import (
    ChatMessagePayload,
    ContextLayerPayload,
    MemoryChatGraphState,
    RetrievedChunkPayload,
)
from app.agent.context import build_memory_chat_prompt_context
from app.agent.model import get_agent_chat_model, get_planner_chat_model
from app.core.timing import elapsed_ms, emit_timing, now_counter
from app.models.chat_message import ChatMessage
from app.models.conversation import Conversation
from app.models.note import utc_now
from app.rag.search import NoteSearchResult, search_notes
from app.rag.chunking.tokenizer import count_tokens
from app.services.long_term_memory_service import list_core_memories


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


AnswerGenerator = Callable[
    [str, list[ChatMessagePayload], list[RetrievedChunkPayload], bool, str],
    str,
]
RetrievalPlanner = Callable[[str, list[ChatMessagePayload]], RetrievalPlan]
NoteRetriever = Callable[..., list[NoteSearchResult]]


def build_load_turn_state_node(
    session_factory: SessionFactory,
    *,
    recent_limit: int = 12,
):
    """读取本轮对话的基础状态。

    参数：
      session_factory: 数据库 session 工厂。
      recent_limit: 读取最近多少条消息。MVP 先按条数限制，后续应按 token budget 裁剪。
    """

    def load_turn_state(state: MemoryChatGraphState) -> MemoryChatGraphState:
        conversation_id = _resolve_conversation_id(state)
        current_message_ids = {
            message_id
            for message_id in [
                state.get("user_message_id"),
                state.get("assistant_message_id"),
            ]
            if message_id
        }
        with session_factory() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                raise ValueError(f"Conversation {conversation_id} not found.")
            messages = session.exec(
                select(ChatMessage)
                .where(ChatMessage.conversation_id == conversation_id)
                .order_by(desc(ChatMessage.created_at), desc(ChatMessage.id))
                .limit(recent_limit)
            ).all()
            recent_messages = [
                _to_message_payload(message)
                for message in sorted(messages, key=lambda item: (item.created_at, item.id or 0))
                if message.id not in current_message_ids and message.status == "completed"
            ]
            return {
                "conversation_id": conversation_id,
                "langgraph_thread_id": conversation.langgraph_thread_id,
                "recent_messages": recent_messages,
                "conversation_summary": conversation.summary,
                # 新一轮输入开始时重置派生字段，避免同一个 thread 的上一轮结果污染本轮。
                "intent": "direct",
                "needs_retrieval": False,
                "needs_query_rewrite": False,
                "retrieved_chunks": [],
                "retrieval_query": "",
                "plan_confidence": 0.0,
                "retrieval_reason": "",
                "retrieval_grade": "none",
                "retrieval_grade_reason": "",
                "retrieval_debug": {},
                "context_l0_layer": {},
                "context_l1_layer": {},
                "context_l2_layer": {},
                "context_l3_layer": {},
                "context_l4_layer": {},
                "prompt_context": "",
                "assistant_answer": "",
                # 保留服务层预创建的消息 ID，最终 persist_messages 会更新这些草稿消息。
                "user_message_id": int(state.get("user_message_id") or 0),
                "assistant_message_id": int(state.get("assistant_message_id") or 0),
                "graph_checkpoint_id": None,
                "error": "",
            }

    return load_turn_state


def dispatch_context_workers(state: MemoryChatGraphState) -> list[Send]:
    """分发 L0-L4 上下文 worker。

    五层上下文彼此没有强依赖，适合用 LangGraph Send 并行执行。
    每个 worker 写入独立 channel，避免 list reducer 在同一 conversation thread
    跨轮追加旧 layer。
    """

    return [
        Send("build_l4_core_memory", state),
        Send("build_l3_retrieved_memory", state),
        Send("build_l2_summary", state),
        Send("build_l1_recent_messages", state),
        Send("build_l0_current_input", state),
    ]


def build_l4_core_memory_node(session_factory: SessionFactory):
    """构建 L4 核心长期记忆层。"""

    def build_l4_core_memory(state: MemoryChatGraphState) -> MemoryChatGraphState:
        with session_factory() as session:
            core_memories = [
                memory.content
                for memory in list_core_memories(session)
            ]
        layer = build_core_memory_layer(core_memories, ContextBudget())
        return {"context_l4_layer": layer.to_payload()}

    return build_l4_core_memory


def build_l3_retrieved_memory_node(
    session_factory: SessionFactory,
    *,
    planner: RetrievalPlanner | None = None,
    retriever: NoteRetriever = search_notes,
    limit: int = 5,
):
    """构建 L3 RAG 检索记忆层。

    L3 是唯一依赖检索规划的金字塔层。为了让主图变扁，plan/retrieve/grade
    都下放到这个 worker 内部执行；L0/L1/L2/L4 不再等待检索链路。
    """

    def build_l3_retrieved_memory(state: MemoryChatGraphState) -> MemoryChatGraphState:
        user_message = _resolve_user_message(state)
        recent_messages = state.get("recent_messages", [])
        total_started_at = now_counter()
        planner_started_at = now_counter()
        plan = (planner or default_retrieval_planner)(user_message, recent_messages)
        planner_elapsed_ms = elapsed_ms(planner_started_at)

        retrieved_chunks: list[RetrievedChunkPayload] = []
        retrieval_grade: Literal["good", "weak", "poor", "none"] = "none"
        retrieval_grade_reason = "本轮未查询个人知识库。"
        retrieval_query = plan.retrieval_query or user_message
        retriever_elapsed_ms = 0
        grade_elapsed_ms = 0
        if plan.needs_retrieval:
            with session_factory() as session:
                retriever_started_at = now_counter()
                results = retriever(session, query=retrieval_query, limit=limit)
                retriever_elapsed_ms = elapsed_ms(retriever_started_at)
            retrieved_chunks = [_to_retrieved_chunk_payload(result) for result in results]
            grade_started_at = now_counter()
            retrieval_grade, retrieval_grade_reason = _grade_retrieval_chunks(retrieved_chunks)
            grade_elapsed_ms = elapsed_ms(grade_started_at)

        layer_started_at = now_counter()
        layer = build_retrieved_memory_layer(
            retrieved_chunks,
            plan.needs_retrieval,
            retrieval_grade,
            ContextBudget(),
        )
        layer_elapsed_ms = elapsed_ms(layer_started_at)
        retrieval_debug = {
            "planner_ms": planner_elapsed_ms,
            "retriever_ms": retriever_elapsed_ms,
            "grade_ms": grade_elapsed_ms,
            "layer_ms": layer_elapsed_ms,
            "total_ms": elapsed_ms(total_started_at),
            "planner_source": plan.source,
            "needs_retrieval": plan.needs_retrieval,
            "retrieval_query": retrieval_query if plan.needs_retrieval else "",
            "retrieved_count": len(retrieved_chunks),
        }
        logger.info("memory_chat.l3_timing %s", retrieval_debug)
        return {
            "intent": plan.intent,
            "needs_retrieval": plan.needs_retrieval,
            "needs_query_rewrite": plan.needs_query_rewrite,
            "retrieval_query": retrieval_query if plan.needs_retrieval else "",
            "plan_confidence": plan.confidence,
            "retrieval_reason": plan.reason,
            "retrieved_chunks": retrieved_chunks,
            "retrieval_grade": retrieval_grade,
            "retrieval_grade_reason": retrieval_grade_reason,
            "retrieval_debug": retrieval_debug,
            "context_l3_layer": layer.to_payload(),
        }

    return build_l3_retrieved_memory


def build_l2_summary_node():
    """构建 L2 对话摘要层。"""

    def build_l2_summary(state: MemoryChatGraphState) -> MemoryChatGraphState:
        layer = build_summary_layer(state.get("conversation_summary", ""), ContextBudget())
        return {"context_l2_layer": layer.to_payload()}

    return build_l2_summary


def build_l1_recent_messages_node():
    """构建 L1 近期对话窗口层。"""

    def build_l1_recent_messages(state: MemoryChatGraphState) -> MemoryChatGraphState:
        layer = build_recent_messages_layer(state.get("recent_messages", []), ContextBudget())
        return {"context_l1_layer": layer.to_payload()}

    return build_l1_recent_messages


def build_l0_current_input_node():
    """构建 L0 当前输入层。"""

    def build_l0_current_input(state: MemoryChatGraphState) -> MemoryChatGraphState:
        layer = build_current_input_layer(_resolve_user_message(state))
        return {"context_l0_layer": layer.to_payload()}

    return build_l0_current_input


def build_merge_prompt_context_node():
    """汇总 L0-L4 worker 结果，生成最终 prompt_context。"""

    def merge_prompt_context(state: MemoryChatGraphState) -> MemoryChatGraphState:
        payloads: list[ContextLayerPayload] = [
            _resolve_context_layer(state, "context_l4_layer"),
            _resolve_context_layer(state, "context_l3_layer"),
            _resolve_context_layer(state, "context_l2_layer"),
            _resolve_context_layer(state, "context_l1_layer"),
            _resolve_context_layer(state, "context_l0_layer"),
        ]
        layers = [context_layer_from_payload(dict(payload)) for payload in payloads]
        context = PyramidPromptContext(layers=layers)
        return {"prompt_context": context.to_prompt()}

    return merge_prompt_context


def build_generate_answer_node(
    answer_generator: AnswerGenerator | None = None,
):
    """生成最终回复。

    回答生成结果会进入 checkpoint。如果模型调用后进程中断，恢复会继续执行
    persist_messages，不会重复调用大模型。
    """

    def generate_answer(state: MemoryChatGraphState) -> MemoryChatGraphState:
        user_message = _resolve_user_message(state)
        recent_messages = state.get("recent_messages", [])
        retrieved_chunks = state.get("retrieved_chunks", [])
        needs_retrieval = bool(state.get("needs_retrieval", False))
        retrieval_grade = state.get("retrieval_grade", "none")
        if answer_generator is None:
            return {
                "assistant_answer": generate_memory_chat_answer(
                    user_message,
                    recent_messages,
                    retrieved_chunks,
                    needs_retrieval,
                    retrieval_grade,
                    prompt_context=state.get("prompt_context", ""),
                )
            }
        generator = answer_generator
        return {
            "assistant_answer": generator(
                user_message,
                recent_messages,
                retrieved_chunks,
                needs_retrieval,
                retrieval_grade,
            )
        }

    return generate_answer


def build_persist_messages_node(session_factory: SessionFactory):
    """把用户消息和 AI 回复写入业务表。

    注意：LangGraph checkpoint 保存的是执行现场；用户可见的消息必须落到 chatmessage。
    流式接口会在 graph 启动前先创建 user/assistant 草稿消息；此节点优先更新草稿。
    非流式接口没有草稿 ID 时，仍沿用创建消息的路径。
    """

    def persist_messages(state: MemoryChatGraphState) -> MemoryChatGraphState:
        conversation_id = _resolve_conversation_id(state)
        user_message = _resolve_user_message(state)
        assistant_answer = state.get("assistant_answer")
        if not assistant_answer:
            raise ValueError("assistant_answer is required before persisting messages.")

        with session_factory() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                raise ValueError(f"Conversation {conversation_id} not found.")

            draft_pair = _load_draft_pair(
                session,
                conversation_id=conversation_id,
                user_message_id=int(state.get("user_message_id") or 0),
                assistant_message_id=int(state.get("assistant_message_id") or 0),
            )
            if draft_pair:
                user, assistant = draft_pair
                user.content = user_message
                user.status = "completed"
                user.token_count = count_tokens(user_message)
                user.updated_at = utc_now()
                assistant.content = assistant_answer
                assistant.status = "completed"
                assistant.token_count = count_tokens(assistant_answer)
                assistant.updated_at = utc_now()
                conversation.updated_at = utc_now()
                session.add(user)
                session.add(assistant)
                session.add(conversation)
                session.commit()
                return {
                    "user_message_id": user.id or 0,
                    "assistant_message_id": assistant.id or 0,
                }

            existing_pair = _find_existing_tail_pair(session, conversation_id, user_message, assistant_answer)
            if existing_pair:
                return {
                    "user_message_id": existing_pair[0],
                    "assistant_message_id": existing_pair[1],
                }

            parent_id = _latest_message_id(session, conversation_id)
            user = ChatMessage(
                conversation_id=conversation_id,
                role="user",
                content=user_message,
                parent_id=parent_id,
                token_count=count_tokens(user_message),
            )
            session.add(user)
            session.flush()
            if user.id is None:
                raise RuntimeError("User message id was not generated.")

            assistant = ChatMessage(
                conversation_id=conversation_id,
                role="assistant",
                content=assistant_answer,
                parent_id=user.id,
                token_count=count_tokens(assistant_answer),
            )
            session.add(assistant)
            session.flush()
            if assistant.id is None:
                raise RuntimeError("Assistant message id was not generated.")

            conversation.updated_at = utc_now()
            session.add(conversation)
            session.commit()
            return {
                "user_message_id": user.id,
                "assistant_message_id": assistant.id,
            }

    return persist_messages


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


def generate_memory_chat_answer(
    user_message: str,
    recent_messages: list[ChatMessagePayload],
    retrieved_chunks: list[RetrievedChunkPayload],
    needs_retrieval: bool,
    retrieval_grade: str,
    *,
    prompt_context: str = "",
) -> str:
    """调用 qwen3.5-plus 生成回答。

    参数：
      user_message: 当前用户输入。
      recent_messages: 当前 conversation 的近期消息。
      retrieved_chunks: RAG 检索命中的笔记 chunk。
      needs_retrieval: 本轮是否被分类为需要个人知识库。
      retrieval_grade: 轻量检索质量评级，决定回答是否应该信任检索结果。
    """

    model = get_agent_chat_model()
    context = prompt_context or build_memory_chat_prompt_context(
        user_message=user_message,
        recent_messages=recent_messages,
        conversation_summary="",
        retrieved_chunks=retrieved_chunks,
        needs_retrieval=needs_retrieval,
        retrieval_grade=retrieval_grade,  # type: ignore[arg-type]
    ).to_prompt()
    response = model.invoke(
        [
            SystemMessage(content=build_memory_chat_answer_system_prompt()),
            HumanMessage(content=context),
        ]
    )
    return str(response.content)


def build_memory_chat_answer_system_prompt() -> str:
    """构建 Memory Chat Graph 的回答提示词。

    这个提示词只约束最终回答节点，不参与 planner 和检索。它的核心目标是：
    1. 继续禁止编造用户记忆；
    2. 避免把每次回答都写成“证据审计报告”；
    3. 对用户画像、偏好、性格这类主观问题，允许自然、温和地表达印象。
    """

    return (
        "你是 Ai 记的记忆精灵，是用户和个人知识库之间的自然交流媒介。\n"
        "你的回答要像熟悉用户的伙伴：真诚、具体、自然，不要像检索报告或审计说明。\n\n"
        "记忆使用规则：\n"
        "- L4 核心长期记忆和 good 检索结果可以作为主要依据。\n"
        "- weak 检索结果可以作为轻量线索，但要用“我目前感觉”“看起来”“可能”这类自然表达，"
        "不要把它包装成确定事实。\n"
        "- poor 或 none 时不要编造用户经历；可以直接基于常识回答，或自然地说明“我现在还没找到相关记忆”。\n"
        "- 不要反复强调“基于有限片段”“检索质量较弱”“记忆质量不足”等内部评估词，"
        "除非用户明确要求调试或追问依据。\n\n"
        "个人画像类问题的风格：\n"
        "- 当用户问“你觉得我是怎样的人”“你了解我吗”“评价我”时，优先给出温和、具体的人格印象。\n"
        "- 可以引用一两个自然证据，但不要机械罗列检索片段。\n"
        "- 可以承认了解还不完整，但只在结尾轻轻带一句，不要把回答开头写成免责声明。\n"
        "- 默认用短段落回答；除非用户要求分析，不要强行编号。\n\n"
        "通用表达规则：\n"
        "- 使用中文。\n"
        "- 回答简洁但有温度。\n"
        "- 如果用户问的是事实型记忆，优先直接给答案，再补充依据。\n"
        "- 不暴露 graph、L0-L4、retrieval_grade、chunk、score 等内部实现细节。"
    )


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


def _resolve_context_layer(
    state: MemoryChatGraphState,
    key: str,
) -> ContextLayerPayload:
    payload = state.get(key)
    if not payload:
        raise ValueError(f"{key} is required before merging prompt context.")
    return payload  # type: ignore[return-value]


def _find_existing_tail_pair(
    session: Session,
    conversation_id: int,
    user_message: str,
    assistant_answer: str,
) -> tuple[int, int] | None:
    messages = session.exec(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(desc(ChatMessage.created_at), desc(ChatMessage.id))
        .limit(2)
    ).all()
    if len(messages) != 2:
        return None
    latest, previous = messages[0], messages[1]
    if (
        previous.role == "user"
        and previous.content == user_message
        and latest.role == "assistant"
        and latest.content == assistant_answer
        and latest.parent_id == previous.id
        and previous.id is not None
        and latest.id is not None
    ):
        return previous.id, latest.id
    return None


def _load_draft_pair(
    session: Session,
    *,
    conversation_id: int,
    user_message_id: int,
    assistant_message_id: int,
) -> tuple[ChatMessage, ChatMessage] | None:
    """读取服务层预创建的一问一答草稿。

    参数：
      session: 当前数据库会话。
      conversation_id: 业务会话 ID，用于防止跨会话误更新。
      user_message_id: 本轮用户消息 ID。
      assistant_message_id: 本轮 assistant 草稿消息 ID。

    返回：
      如果两条消息都存在且属于同一会话，则返回二元组；否则返回 None。
    """

    if not user_message_id or not assistant_message_id:
        return None
    user = session.get(ChatMessage, user_message_id)
    assistant = session.get(ChatMessage, assistant_message_id)
    if (
        user is None
        or assistant is None
        or user.conversation_id != conversation_id
        or assistant.conversation_id != conversation_id
        or user.role != "user"
        or assistant.role != "assistant"
    ):
        return None
    return user, assistant


def _latest_message_id(session: Session, conversation_id: int) -> int | None:
    message = session.exec(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(desc(ChatMessage.created_at), desc(ChatMessage.id))
    ).first()
    return message.id if message else None


def _to_message_payload(message: ChatMessage) -> ChatMessagePayload:
    return {
        "id": message.id or 0,
        "role": message.role,
        "content": message.content,
        "token_count": message.token_count,
    }


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


def _resolve_conversation_id(state: MemoryChatGraphState) -> int:
    conversation_id = state.get("conversation_id")
    if conversation_id is None:
        raise ValueError("conversation_id is required.")
    return int(conversation_id)


def _resolve_user_message(state: MemoryChatGraphState) -> str:
    user_message = state.get("user_message", "").strip()
    if not user_message:
        raise ValueError("user_message is required.")
    return user_message
