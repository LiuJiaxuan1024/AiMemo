from typing import TypedDict


class ConversationTitleGraphState(TypedDict, total=False):
    """conversation_title_graph 的共享状态。

    `needs_title` 控制条件边：title 已被设置过的话直接结束。
    `generated_title` 进入 checkpoint，恢复时不必重复调用 LLM。
    """

    job_id: int
    conversation_id: int
    first_user_message: str
    needs_title: bool
    generated_title: str
