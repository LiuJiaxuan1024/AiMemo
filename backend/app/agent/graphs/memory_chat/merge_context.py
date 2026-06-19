from app.agent.context import PyramidPromptContext, context_layer_from_payload
from app.agent.graphs.memory_chat.state import ContextLayerPayload, MemoryChatGraphState


def build_merge_prompt_context_node():
    """汇总上下文 worker 结果，生成最终 prompt_context。

    L1 历史消息和 L0 当前输入必须分开注入。之前把 L1+L0 合成“连续对话窗口”，
    容易让工具 planner 把历史 assistant 草稿误当成本轮指令，导致跨任务串工具。
    """

    def merge_prompt_context(state: MemoryChatGraphState) -> MemoryChatGraphState:
        payloads: list[ContextLayerPayload] = [
            _resolve_context_layer(state, "context_l4_layer"),
            _resolve_context_layer(state, "context_l3_knowledge_layer"),
            _resolve_context_layer(state, "context_l3_layer"),
            _resolve_context_layer(state, "context_l2_layer"),
            _resolve_context_layer(state, "context_l1_layer"),
            _resolve_context_layer(state, "context_lx_attachment_layer"),
            _resolve_context_layer(state, "context_lx_web_layer"),
            _resolve_context_layer(state, "context_l0_adjacent_layer"),
            _resolve_context_layer(state, "context_l0_layer"),
        ]
        layers = [context_layer_from_payload(dict(payload)) for payload in payloads]
        context = PyramidPromptContext(layers=layers)
        prompt_context = context.to_prompt()
        return {"prompt_context": prompt_context}

    return merge_prompt_context


def _resolve_context_layer(
    state: MemoryChatGraphState,
    key: str,
) -> ContextLayerPayload:
    payload = state.get(key)
    if not payload:
        raise ValueError(f"{key} is required before merging prompt context.")
    return payload  # type: ignore[return-value]
