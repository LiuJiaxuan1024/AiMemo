from typing import TypedDict

from app.agent.graphs.memory_chat.state import ChatMessagePayload


class ConversationSummaryGraphState(TypedDict, total=False):
    """conversation_summary_graph 的共享状态。

    job_id 用来绑定外层 job；conversation_id 是本 graph 唯一允许更新的业务对象。
    generated_summary 会进入 checkpoint，恢复时可以直接写库，不必重复调用 LLM。
    """

    job_id: int
    conversation_id: int
    old_summary: str
    new_messages: list[ChatMessagePayload]
    new_token_count: int
    last_message_id: int | None
    needs_summary: bool
    generated_summary: str
