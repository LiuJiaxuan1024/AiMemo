from fastapi import HTTPException, status
from sqlmodel import Session, desc, select

from app.models.chat_message import ChatMessage
from app.models.chat_turn import ChatTurn
from app.models.conversation import Conversation
from app.models.note import utc_now
from app.rag.chunking.tokenizer import count_tokens
from app.schemas.conversation import (
    ChatMessageCreate,
    ChatMessageRead,
    ConversationCreate,
    ConversationListItem,
    ConversationRead,
)


def create_conversation(session: Session, payload: ConversationCreate) -> ConversationRead:
    """创建一个业务对话线程。

    参数：
      session: 当前数据库会话。
      payload: 创建请求，当前只包含可选标题。

    返回：
      ConversationRead。创建后会立即写入 langgraph_thread_id，格式为 conversation:{id}。
    """

    title = payload.title.strip() or "新对话"
    conversation = Conversation(title=title)
    session.add(conversation)
    session.flush()
    if conversation.id is None:
        raise RuntimeError("Conversation id was not generated.")
    conversation.langgraph_thread_id = f"conversation:{conversation.id}"
    session.add(conversation)
    session.commit()
    session.refresh(conversation)
    return _to_conversation_read(conversation)


def list_conversations(session: Session) -> list[ConversationListItem]:
    """按更新时间倒序返回对话列表。"""

    conversations = session.exec(select(Conversation).order_by(desc(Conversation.updated_at))).all()
    return [_to_conversation_list_item(conversation) for conversation in conversations]


def get_conversation(session: Session, conversation_id: int) -> ConversationRead:
    """读取单个对话，不存在时返回 404。"""

    return _to_conversation_read(_get_conversation_or_404(session, conversation_id))


def list_messages(session: Session, conversation_id: int) -> list[ChatMessageRead]:
    """读取某个对话的消息。

    MVP 阶段按创建顺序展示线性消息；后续做状态树 UI 时，可以按 parent_id 组装树。
    """

    _get_conversation_or_404(session, conversation_id)
    messages = session.exec(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(ChatMessage.created_at, ChatMessage.id)
    ).all()
    turn_ids_by_assistant_message_id = _load_turn_ids_by_assistant_message_id(
        session,
        conversation_id=conversation_id,
    )
    return [
        _to_chat_message_read(
            message,
            turn_id=turn_ids_by_assistant_message_id.get(message.id or 0),
        )
        for message in messages
    ]


def append_message(
    session: Session,
    conversation_id: int,
    payload: ChatMessageCreate,
) -> ChatMessageRead:
    """向对话追加一条消息。

    参数：
      session: 当前数据库会话。
      conversation_id: 消息所属对话。
      payload: 消息内容、角色、可选 parent_id/checkpoint_id。

    行为：
      - 如果 parent_id 为空，默认接在当前会话最后一条消息后。
      - 如果 parent_id 不为空，会验证父消息属于同一个 conversation。
      - token_count 使用本地 tokenizer 粗略计算，后续用于上下文预算。
    """

    conversation = _get_conversation_or_404(session, conversation_id)
    parent_id = payload.parent_id
    if parent_id is None:
        parent_id = _latest_message_id(session, conversation_id)
    else:
        _ensure_parent_message_belongs_to_conversation(session, conversation_id, parent_id)

    message = ChatMessage(
        conversation_id=conversation_id,
        role=payload.role,
        content=payload.content,
        parent_id=parent_id,
        checkpoint_id=payload.checkpoint_id,
        status=payload.status,
        token_count=count_tokens(payload.content),
    )
    session.add(message)
    conversation.updated_at = utc_now()
    session.add(conversation)
    session.commit()
    session.refresh(message)
    return _to_chat_message_read(message)


def _get_conversation_or_404(session: Session, conversation_id: int) -> Conversation:
    conversation = session.get(Conversation, conversation_id)
    if conversation is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Conversation not found",
        )
    return conversation


def _latest_message_id(session: Session, conversation_id: int) -> int | None:
    message = session.exec(
        select(ChatMessage)
        .where(ChatMessage.conversation_id == conversation_id)
        .order_by(desc(ChatMessage.created_at), desc(ChatMessage.id))
    ).first()
    return message.id if message else None


def _ensure_parent_message_belongs_to_conversation(
    session: Session,
    conversation_id: int,
    parent_id: int,
) -> None:
    parent = session.get(ChatMessage, parent_id)
    if parent is None or parent.conversation_id != conversation_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="parent_id must reference a message in the same conversation",
        )


def _to_conversation_read(conversation: Conversation) -> ConversationRead:
    return ConversationRead(
        id=conversation.id or 0,
        title=conversation.title,
        status=conversation.status,
        summary=conversation.summary,
        summary_message_id=conversation.summary_message_id,
        langgraph_thread_id=conversation.langgraph_thread_id,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def _to_conversation_list_item(conversation: Conversation) -> ConversationListItem:
    return ConversationListItem(
        id=conversation.id or 0,
        title=conversation.title,
        status=conversation.status,
        summary=conversation.summary,
        summary_message_id=conversation.summary_message_id,
        langgraph_thread_id=conversation.langgraph_thread_id,
        created_at=conversation.created_at,
        updated_at=conversation.updated_at,
    )


def _load_turn_ids_by_assistant_message_id(
    session: Session,
    *,
    conversation_id: int,
) -> dict[int, int]:
    """读取 assistant message 与 ChatTurn 的映射。

    Graph 调试视图是以 `ChatTurn` 为事实来源的。前端只有拿到明确的 turn_id，
    才应该显示 graph 按钮；否则历史消息、摘要消息或外部入口消息会误触发 404。
    """

    turns = session.exec(
        select(ChatTurn).where(
            ChatTurn.conversation_id == conversation_id,
            ChatTurn.assistant_message_id.is_not(None),
        )
    ).all()
    return {
        int(turn.assistant_message_id): int(turn.id)
        for turn in turns
        if turn.id is not None and turn.assistant_message_id is not None
    }


def _to_chat_message_read(message: ChatMessage, *, turn_id: int | None = None) -> ChatMessageRead:
    return ChatMessageRead(
        id=message.id or 0,
        conversation_id=message.conversation_id,
        role=message.role,
        content=message.content,
        parent_id=message.parent_id,
        checkpoint_id=message.checkpoint_id,
        status=message.status,
        token_count=message.token_count,
        turn_id=turn_id,
        created_at=message.created_at,
        updated_at=message.updated_at,
    )
