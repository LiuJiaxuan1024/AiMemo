from collections.abc import Callable
from contextlib import AbstractContextManager

from sqlmodel import Session, desc, select

from app.core.database import session_scope
from app.models.conversation import Conversation
from app.schemas.conversation import ConversationCreate
from app.services.chat_service import stream_conversation_chat_events
from app.services.conversation_service import create_conversation


SessionFactory = Callable[[], AbstractContextManager[Session]]

ELF_CONVERSATION_TITLE = "Memo Elf"


def get_or_create_elf_conversation(
    *,
    session_factory: SessionFactory = session_scope,
) -> Conversation:
    """读取或创建桌面精灵专用对话。

    外置精灵聊天和 AiMemo 内置聊天复用同一套 Memory Chat Graph，但业务入口需要一条
    稳定的 conversation，用来保存上下文、checkpoint thread 和后续对话状态树。
    """

    with session_factory() as session:
        conversation = session.exec(
            select(Conversation)
            .where(Conversation.title == ELF_CONVERSATION_TITLE, Conversation.status == "active")
            .order_by(desc(Conversation.updated_at), desc(Conversation.id))
        ).first()
        if conversation is not None:
            return conversation

        created = create_conversation(session, ConversationCreate(title=ELF_CONVERSATION_TITLE))
        conversation = session.get(Conversation, created.id)
        if conversation is None:
            raise RuntimeError("Elf conversation was not created.")
        return conversation


def stream_elf_chat_events(
    *,
    message: str,
    session_factory: SessionFactory = session_scope,
    checkpoint_path: str | None = None,
):
    """桌面精灵聊天 SSE。

    它内部仍执行 memory_chat_graph，但关闭精灵状态播报。消费端只关心回答文本，
    不需要看到“开始检索/开始写入记忆”等工作型气泡。
    """

    conversation = get_or_create_elf_conversation(session_factory=session_factory)
    conversation_id = conversation.id
    if conversation_id is None:
        raise RuntimeError("Elf conversation id is required.")

    yield from stream_conversation_chat_events(
        conversation_id,
        message=message,
        session_factory=session_factory,
        checkpoint_path=checkpoint_path,
        emit_status_events=False,
        answer_mode="elf_bubble",
    )
