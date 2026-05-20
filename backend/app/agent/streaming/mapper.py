from typing import Any

from app.agent.streaming.events import AiJiStreamEvent


VISIBLE_ANSWER_NODE = "generate_answer"
VISIBLE_BUBBLE_NODE = "generate_elf_bubble_answer"


def map_langgraph_stream_chunk(
    mode: str,
    chunk: Any,
    *,
    visible_answer_node: str = VISIBLE_ANSWER_NODE,
    visible_bubble_node: str = VISIBLE_BUBBLE_NODE,
) -> list[AiJiStreamEvent]:
    """把 LangGraph 原始 stream chunk 映射为 Ai 记内部事件。

    参数：
      mode: LangGraph 返回的 stream mode，例如 updates/messages。
      chunk: 该 mode 对应的原始 chunk。
      visible_answer_node: 哪个节点的 LLM token 可以暴露给用户。

    返回：
      AiJiStreamEvent 列表。updates 可能一次包含多个节点更新，所以返回 list。

    设计原则：
      - updates -> node，用于 graph 可视化和 ChatTurn 节点状态。
      - messages -> answer_delta/internal_token，用于区分用户可见回答和内部 LLM 调用。
      - 未识别事件暂时忽略，后续接 custom/debug/events 时再扩展。
    """

    if mode == "updates":
        return _map_updates_chunk(chunk)
    if mode in {"messages", "messages-tuple"}:
        event = _map_messages_chunk(
            chunk,
            visible_answer_node=visible_answer_node,
            visible_bubble_node=visible_bubble_node,
        )
        return [event] if event else []
    return []


def _map_updates_chunk(chunk: Any) -> list[AiJiStreamEvent]:
    if not isinstance(chunk, dict):
        return []

    events: list[AiJiStreamEvent] = []
    for node_name, state_update in chunk.items():
        if not isinstance(node_name, str):
            continue
        events.append(
            {
                "event": "node",
                "node": node_name,
                "state_update": state_update if isinstance(state_update, dict) else {},
            }
        )
    return events


def _map_messages_chunk(
    chunk: Any,
    *,
    visible_answer_node: str,
    visible_bubble_node: str,
) -> AiJiStreamEvent | None:
    if not isinstance(chunk, tuple) or len(chunk) != 2:
        return None

    token_chunk, metadata = chunk
    if not isinstance(metadata, dict):
        metadata = {}
    node_name = str(metadata.get("langgraph_node") or "")
    content = _extract_token_content(token_chunk)
    if not content:
        return None

    if node_name == visible_answer_node:
        return {
            "event": "answer_delta",
            "node": node_name,
            "content": content,
            "metadata": metadata,
        }
    if node_name == visible_bubble_node:
        return {
            "event": "bubble_delta",
            "node": node_name,
            "content": content,
            "metadata": metadata,
        }
    return {
        "event": "internal_token",
        "node": node_name,
        "content": content,
        "metadata": metadata,
    }


def _extract_token_content(token_chunk: Any) -> str:
    """从 LangChain message chunk 中提取文本。

    不同模型/版本的 chunk.content 可能是字符串，也可能是分段 list。
    这里尽量只提取纯文本，避免把工具调用、结构化块等内部数据暴露给用户。
    """

    content = getattr(token_chunk, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and item.get("type") == "text":
                text = item.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "".join(parts)
    return ""
