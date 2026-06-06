from collections.abc import Callable
from contextlib import AbstractContextManager

from langchain_core.messages import HumanMessage, SystemMessage
from sqlmodel import Session, col, select

from app.agent.graphs.conversation_summary.state import ConversationSummaryGraphState
from app.agent.graphs.memory_chat.state import ChatMessagePayload
from app.models.chat_message import ChatMessage
from app.models.conversation import Conversation
from app.models.note import utc_now
from app.rag.chunking.tokenizer import count_tokens


SessionFactory = Callable[[], AbstractContextManager[Session]]
SummaryGenerator = Callable[[str, list[ChatMessagePayload]], str]


DEFAULT_SUMMARY_TRIGGER_TOKENS = 1500


def build_load_summary_inputs_node(
    session_factory: SessionFactory,
    *,
    trigger_tokens: int = DEFAULT_SUMMARY_TRIGGER_TOKENS,
):
    """读取本次滚动摘要需要处理的消息。

    参数：
      session_factory: 数据库 session 工厂。
      trigger_tokens: 未摘要消息 token 超过该阈值才真正进入摘要生成。

    该节点是幂等的：重复执行只会重新读取当前业务表状态，不写入数据。
    """

    def load_summary_inputs(
        state: ConversationSummaryGraphState,
    ) -> ConversationSummaryGraphState:
        conversation_id = _resolve_conversation_id(state)
        with session_factory() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                raise ValueError(f"Conversation {conversation_id} not found.")

            messages = _load_unsummarized_messages(session, conversation)
            payloads = [_to_message_payload(message) for message in messages]
            token_count = sum(_message_token_count(message) for message in messages)
            last_message_id = messages[-1].id if messages else None

            return {
                "conversation_id": conversation_id,
                "old_summary": conversation.summary or "",
                "new_messages": payloads,
                "new_token_count": token_count,
                "last_message_id": last_message_id,
                "needs_summary": bool(messages and token_count > trigger_tokens),
                "generated_summary": "",
            }

    return load_summary_inputs


def build_summarize_messages_node(
    summary_generator: SummaryGenerator | None = None,
):
    """调用模型生成新的滚动摘要。

    生成结果会写入 checkpoint。如果该节点完成后进程中断，恢复时会从
    persist_summary 继续，不会再次消耗模型调用。
    """

    def summarize_messages(
        state: ConversationSummaryGraphState,
    ) -> ConversationSummaryGraphState:
        messages = state.get("new_messages", [])
        if not messages:
            raise ValueError("new_messages is required before summarizing.")
        generator = summary_generator or generate_conversation_summary
        return {
            "generated_summary": generator(
                state.get("old_summary", ""),
                messages,
            )
        }

    return summarize_messages


def build_persist_summary_node(session_factory: SessionFactory):
    """把滚动摘要写回 conversation。

    写库前再次检查 summary_message_id，避免 job 重试、恢复或并发执行时重复覆盖
    已经推进过的摘要。
    """

    def persist_summary(
        state: ConversationSummaryGraphState,
    ) -> ConversationSummaryGraphState:
        conversation_id = _resolve_conversation_id(state)
        last_message_id = state.get("last_message_id")
        generated_summary = state.get("generated_summary", "").strip()
        if not last_message_id:
            return {}
        if not generated_summary:
            raise ValueError("generated_summary is required before persisting summary.")

        with session_factory() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                raise ValueError(f"Conversation {conversation_id} not found.")
            if (
                conversation.summary_message_id is not None
                and conversation.summary_message_id >= last_message_id
            ):
                return {}

            conversation.summary = generated_summary
            conversation.summary_message_id = last_message_id
            conversation.updated_at = utc_now()
            session.add(conversation)
            session.commit()
        return {}

    return persist_summary


def generate_conversation_summary(
    old_summary: str,
    new_messages: list[ChatMessagePayload],
) -> str:
    """使用 qwen3.5-plus 生成面向 L2 的滚动对话摘要。

    L2 摘要只服务当前 conversation 的上下文压缩，不等同于 L4 长期核心记忆。
    稳定偏好、身份和长期目标后续应由独立长期记忆 graph 抽取。
    """

    from app.agent.model import get_agent_chat_model

    old_summary_text = old_summary.strip() or "暂无旧摘要。"
    messages_text = "\n".join(
        f"{message['role']}: {message['content']}" for message in new_messages
    )
    response = get_agent_chat_model().invoke(
        [
            SystemMessage(
                content=(
                    "你是 Ai 记的对话摘要器。你的任务是把当前 conversation 的新增消息"
                    "合并进旧摘要，生成一份简洁、稳定、适合后续对话继续使用的滚动摘要。"
                    "保留用户当前目标、计划、偏好、未解决问题和重要事实；"
                    "忽略寒暄、重复表达和无意义细节。不要编造。使用中文。"
                )
            ),
            HumanMessage(
                content=(
                    f"旧摘要：\n{old_summary_text}\n\n"
                    f"新增消息：\n{messages_text}\n\n"
                    "请输出更新后的摘要正文，不要输出 JSON。"
                )
            ),
        ]
    )
    return str(response.content).strip()


def route_after_load_summary(state: ConversationSummaryGraphState) -> str:
    """条件边：只有未摘要内容超过阈值时才进入 LLM 摘要。"""

    return "summarize_messages" if state.get("needs_summary") else "__end__"


def _load_unsummarized_messages(
    session: Session,
    conversation: Conversation,
) -> list[ChatMessage]:
    statement = (
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation.id)
        .where(ChatMessage.status == "completed")
        .where(col(ChatMessage.role).in_(["user", "assistant"]))
        .order_by(ChatMessage.id)
    )
    if conversation.summary_message_id is not None:
        statement = statement.where(ChatMessage.id > conversation.summary_message_id)
    return list(session.exec(statement).all())


def _to_message_payload(message: ChatMessage) -> ChatMessagePayload:
    return {
        "id": message.id or 0,
        "role": message.role,
        "content": message.content,
        "token_count": _message_token_count(message),
    }


def _message_token_count(message: ChatMessage) -> int:
    return message.token_count if message.token_count > 0 else count_tokens(message.content)


def _resolve_conversation_id(state: ConversationSummaryGraphState) -> int:
    conversation_id = state.get("conversation_id")
    if conversation_id is None:
        raise ValueError("conversation_id is required.")
    return int(conversation_id)
