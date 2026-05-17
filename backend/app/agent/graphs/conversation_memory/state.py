from typing import TypedDict

from app.agent.graphs.memory_chat.state import ChatMessagePayload


class ConversationMemoryGraphState(TypedDict, total=False):
    """conversation_memory_graph 的共享状态。

    extraction_result 会进入 checkpoint。若 LLM 抽取完成后进程中断，
    恢复时可以直接写 longtermmemory，不重复调用模型。
    """

    job_id: int
    conversation_id: int
    user_message_id: int
    assistant_message_id: int
    source_messages: list[ChatMessagePayload]
    extraction_result: dict
    written_memory_ids: list[int]
