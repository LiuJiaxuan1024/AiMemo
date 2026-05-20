from typing import Any, Literal, TypedDict


class NodeUpdateEvent(TypedDict):
    """LangGraph 节点状态更新事件。

    参数：
      event: 固定为 node。
      node: 产出 update 的 LangGraph 节点名。
      state_update: 该节点本次写入 state 的局部更新。
    """

    event: Literal["node"]
    node: str
    state_update: dict[str, Any]


class AnswerDeltaEvent(TypedDict):
    """用户可见的回答 token 事件。

    只有 `generate_answer` 节点产生的 LLM token 会被映射为该事件。
    其他内部 LLM token 会走 InternalTokenEvent，默认不暴露给前端。
    """

    event: Literal["answer_delta"]
    node: str
    content: str
    metadata: dict[str, Any]


class BubbleDeltaEvent(TypedDict):
    """外置精灵气泡回答 token 事件。

    只有 `generate_elf_bubble_answer` 节点产生的 LLM token 会被映射为该事件。
    第一版 token 内容仍是 JSON 片段，service 会在 done 时提供最终 bubbles。
    """

    event: Literal["bubble_delta"]
    node: str
    content: str
    metadata: dict[str, Any]


class InternalTokenEvent(TypedDict):
    """内部 LLM token 事件。

    例如 L3 planner 的 JSON token。第一版不发给前端，但保留类型，
    方便后续调试模式展示“内部 agent 正在做什么”。
    """

    event: Literal["internal_token"]
    node: str
    content: str
    metadata: dict[str, Any]


AiJiStreamEvent = NodeUpdateEvent | AnswerDeltaEvent | BubbleDeltaEvent | InternalTokenEvent
