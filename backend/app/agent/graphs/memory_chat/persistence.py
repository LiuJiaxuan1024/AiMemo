from collections.abc import Callable
from contextlib import AbstractContextManager

from sqlmodel import Session, desc, select

from app.agent.graphs.memory_chat.state import ChatMessagePayload, MemoryChatGraphState
from app.models.chat_message import ChatMessage
from app.models.conversation import Conversation
from app.models.note import utc_now
from app.rag.chunking.tokenizer import count_tokens
from app.services.attachment_service import attach_attachments_to_message


SessionFactory = Callable[[], AbstractContextManager[Session]]


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
                conversation.active_task = ""
                session.add(user)
                session.add(assistant)
                session.add(conversation)
                attach_attachments_to_message(
                    session,
                    conversation_id=conversation_id,
                    message_id=user.id or 0,
                    attachment_ids=list(state.get("attachment_ids") or []),
                )
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

            parent_id = int(state.get("parent_message_id") or 0) or _latest_message_id(session, conversation_id)
            if parent_id is not None:
                parent = session.get(ChatMessage, parent_id)
                if parent is None or parent.conversation_id != conversation_id:
                    raise ValueError("parent_message_id must reference a message in the same conversation.")
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
            attach_attachments_to_message(
                session,
                conversation_id=conversation_id,
                message_id=user.id,
                attachment_ids=list(state.get("attachment_ids") or []),
            )

            conversation.updated_at = utc_now()
            conversation.active_task = ""
            session.add(conversation)
            session.commit()
            return {
                "user_message_id": user.id,
                "assistant_message_id": assistant.id,
            }

    return persist_messages




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


