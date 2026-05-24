from collections.abc import Callable
from contextlib import AbstractContextManager

from langchain_core.messages import HumanMessage, SystemMessage
from sqlmodel import Session, select

from app.agent.graphs.conversation_title.state import ConversationTitleGraphState
from app.agent.model import get_agent_chat_model
from app.models.chat_message import ChatMessage
from app.models.conversation import Conversation
from app.models.note import utc_now


SessionFactory = Callable[[], AbstractContextManager[Session]]
TitleGenerator = Callable[[str], str]


DEFAULT_TITLE = "新对话"
MAX_TITLE_LENGTH = 24


def build_load_title_inputs_node(session_factory: SessionFactory):
    """读取首条 user 消息；如果 title 已被设过就 skip。

    幂等：重复执行只读不写。
    """

    def load_title_inputs(
        state: ConversationTitleGraphState,
    ) -> ConversationTitleGraphState:
        conversation_id = _resolve_conversation_id(state)
        with session_factory() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                raise ValueError(f"Conversation {conversation_id} not found.")
            if conversation.title and conversation.title != DEFAULT_TITLE:
                return {
                    "conversation_id": conversation_id,
                    "first_user_message": "",
                    "needs_title": False,
                    "generated_title": "",
                }

            first_user = session.exec(
                select(ChatMessage)
                .where(ChatMessage.conversation_id == conversation_id)
                .where(ChatMessage.role == "user")
                .where(ChatMessage.status == "completed")
                .order_by(ChatMessage.id)
                .limit(1)
            ).first()
            content = (first_user.content if first_user else "").strip()
            return {
                "conversation_id": conversation_id,
                "first_user_message": content,
                "needs_title": bool(content),
                "generated_title": "",
            }

    return load_title_inputs


def build_generate_title_node(title_generator: TitleGenerator | None = None):
    """调用 LLM 生成短标题。失败时降级为前 12 字 + …。"""

    def generate_title(
        state: ConversationTitleGraphState,
    ) -> ConversationTitleGraphState:
        message = state.get("first_user_message", "").strip()
        if not message:
            return {"generated_title": ""}
        generator = title_generator or generate_conversation_title
        try:
            title = generator(message)
        except Exception:
            title = _fallback_title(message)
        title = _normalize_title(title) or _fallback_title(message)
        return {"generated_title": title}

    return generate_title


def build_persist_title_node(session_factory: SessionFactory):
    """把生成的 title 写回 conversation；写库前再判一次幂等。"""

    def persist_title(
        state: ConversationTitleGraphState,
    ) -> ConversationTitleGraphState:
        title = state.get("generated_title", "").strip()
        if not title:
            return {}
        conversation_id = _resolve_conversation_id(state)
        with session_factory() as session:
            conversation = session.get(Conversation, conversation_id)
            if conversation is None:
                return {}
            if conversation.title and conversation.title != DEFAULT_TITLE:
                # 已被人工命名或其他 job 写过，不覆盖
                return {}
            conversation.title = title
            conversation.updated_at = utc_now()
            session.add(conversation)
            session.commit()
        return {}

    return persist_title


def route_after_load_title(state: ConversationTitleGraphState) -> str:
    return "generate_title" if state.get("needs_title") else "__end__"


def generate_conversation_title(first_user_message: str) -> str:
    """用 qwen3.5-plus 给会话生成一个 ≤ 16 字的中文短标题。"""

    response = get_agent_chat_model().invoke(
        [
            SystemMessage(
                content=(
                    "你是 Ai 记的对话命名器。根据用户发送的第一条消息，"
                    "为这次对话起一个简短、贴切的中文标题。"
                    "要求：长度不超过 16 个字，不要使用引号、标点符号、emoji，"
                    "不要加任何前缀如『标题：』，直接输出标题本身。"
                )
            ),
            HumanMessage(content=f"用户的第一句话：\n{first_user_message.strip()}"),
        ]
    )
    return str(response.content).strip()


def _normalize_title(raw: str) -> str:
    title = raw.strip().strip("\"'“”‘’《》【】")
    # 去掉可能的"标题："前缀
    for prefix in ("标题：", "标题:", "Title:", "title:"):
        if title.startswith(prefix):
            title = title[len(prefix) :].strip()
    # 截断到 MAX_TITLE_LENGTH
    if len(title) > MAX_TITLE_LENGTH:
        title = title[: MAX_TITLE_LENGTH - 1].rstrip() + "…"
    return title


def _fallback_title(message: str) -> str:
    snippet = message.strip().replace("\n", " ")
    if len(snippet) <= 12:
        return snippet or DEFAULT_TITLE
    return snippet[:12] + "…"


def _resolve_conversation_id(state: ConversationTitleGraphState) -> int:
    conversation_id = state.get("conversation_id")
    if conversation_id is None:
        raise ValueError("conversation_id is required.")
    return int(conversation_id)
