from dataclasses import dataclass, field
from typing import Any, Literal

from app.rag.chunking.tokenizer import count_tokens


RetrievalGrade = Literal["good", "weak", "poor", "none"]


@dataclass(frozen=True)
class ContextBudget:
    """记忆金字塔每一层的 token 预算。

    参数：
      core_memory_tokens: L4 核心长期记忆预算，数量少但优先级最高。
      retrieved_memory_tokens: L3 RAG 检索记忆预算，通常最占空间。
      summary_tokens: L2 对话摘要预算，用于承接较早上下文。
      recent_message_tokens: L1+L0 当前对话窗口预算。
      weak_retrieval_max_chunks: weak 检索结果最多放入多少条，避免弱相关内容污染回答。
    """

    core_memory_tokens: int = 300
    retrieved_memory_tokens: int = 1200
    summary_tokens: int = 500
    conversation_window_tokens: int = 1200
    recent_message_tokens: int = 1000
    weak_retrieval_max_chunks: int = 3


@dataclass(frozen=True)
class ContextLayer:
    """最终 Prompt 中的一层上下文。

    参数：
      level: 金字塔层级，L4 最高、L0 最贴近当前输入。
      name: 层名称，用于 prompt 标题和调试展示。
      content: 已经过预算裁剪后的文本。
      budget_tokens: 该层预算。
      used_tokens: 该层实际估算 token 数。
      note: 给模型和开发者看的使用说明，例如“weak 检索只能谨慎参考”。
    """

    level: int
    name: str
    content: str
    budget_tokens: int | None
    used_tokens: int
    note: str = ""

    def to_prompt_section(self) -> str:
        """把单层上下文渲染成稳定的 prompt 片段。"""

        title = f"L{self.level} {self.name}"
        note = f"\n说明：{self.note}" if self.note else ""
        return f"## {title}{note}\n{self.content}"

    def to_payload(self) -> dict[str, Any]:
        """转换为可写入 LangGraph checkpoint 的普通 dict。"""

        return {
            "level": self.level,
            "name": self.name,
            "content": self.content,
            "budget_tokens": self.budget_tokens,
            "used_tokens": self.used_tokens,
            "note": self.note,
        }


@dataclass(frozen=True)
class PyramidPromptContext:
    """回答节点消费的完整金字塔上下文。

    layers 使用高层到低层排序，让模型先看到最稳定、最重要的信息，
    最后再看到当前用户输入。
    """

    layers: list[ContextLayer] = field(default_factory=list)

    @property
    def total_tokens(self) -> int:
        return sum(layer.used_tokens for layer in self.layers)

    def to_prompt(self) -> str:
        """渲染给 LLM 的最终上下文文本。"""

        sections = "\n\n".join(layer.to_prompt_section() for layer in self.layers)
        return (
            "下面是 Ai 记为本轮回答构建的分层上下文。"
            "越靠上的层级越稳定、越重要；越靠下越贴近当前对话。\n\n"
            f"{sections}"
        )


def build_memory_chat_prompt_context(
    *,
    user_message: str,
    recent_messages: list[dict[str, Any]],
    conversation_summary: str | None,
    retrieved_chunks: list[dict[str, Any]],
    needs_retrieval: bool,
    retrieval_grade: RetrievalGrade,
    core_memories: list[str] | None = None,
    budget: ContextBudget | None = None,
) -> PyramidPromptContext:
    """构建 memory_chat_graph 的金字塔上下文。

    参数：
      user_message: 当前用户输入，会和近期消息合并成 L1+L0 当前对话窗口。
      recent_messages: 近期对话消息，通常来自 chatmessage 表。
      conversation_summary: L2 对话摘要；当前只读取已有摘要，不负责生成摘要。
      retrieved_chunks: L3 向量检索命中的 note chunk。
      needs_retrieval: 本轮是否计划查询个人知识库。
      retrieval_grade: 检索质量评级，决定 L3 是否可信。
      core_memories: L4 核心长期记忆；当前还没有表结构，先保留扩展入口。
      budget: 每层 token 预算；测试和后续配置可以显式传入。
    """

    selected_budget = budget or ContextBudget()
    layers = [
        build_core_memory_layer(core_memories or [], selected_budget),
        build_retrieved_memory_layer(
            retrieved_chunks,
            needs_retrieval,
            retrieval_grade,
            selected_budget,
        ),
        build_summary_layer(conversation_summary, selected_budget),
        build_current_conversation_window_layer(
            recent_messages,
            user_message,
            selected_budget,
        ),
    ]
    return PyramidPromptContext(layers=layers)


def build_core_memory_layer(
    core_memories: list[str],
    budget: ContextBudget,
) -> ContextLayer:
    """构建 L4 核心长期记忆层。

    第一阶段还没有长期记忆表，因此多数情况下会输出占位说明。
    保留该层可以让 prompt 结构先稳定下来，后续接表和 worker 时不用改回答节点。
    """

    if not core_memories:
        content = "暂无已整理的核心长期记忆。"
    else:
        content = _fit_lines_to_budget(
            [f"- {memory}" for memory in core_memories],
            budget.core_memory_tokens,
        )
    return ContextLayer(
        level=4,
        name="核心长期记忆",
        content=content,
        budget_tokens=budget.core_memory_tokens,
        used_tokens=count_tokens(content),
        note="数量最少、稳定性最高；如果存在，应优先遵守。",
    )


def build_retrieved_memory_layer(
    retrieved_chunks: list[dict[str, Any]],
    needs_retrieval: bool,
    retrieval_grade: RetrievalGrade,
    budget: ContextBudget,
) -> ContextLayer:
    """构建 L3 RAG 检索记忆层。

    good 可以作为主要依据；weak 只放少量候选并提醒谨慎；
    poor/none 不把 chunk 暴露给模型，避免弱相关内容诱导编造。
    """

    note = f"检索质量：{retrieval_grade}。"
    selected_chunks = retrieved_chunks
    if retrieval_grade == "good" and retrieved_chunks:
        content = _fit_lines_to_budget(
            [_format_chunk(chunk) for chunk in selected_chunks],
            budget.retrieved_memory_tokens,
        )
        note += "可作为主要依据，但仍需避免超出原文事实。"
    elif retrieval_grade == "weak" and retrieved_chunks:
        selected_chunks = retrieved_chunks[: budget.weak_retrieval_max_chunks]
        content = _fit_lines_to_budget(
            [_format_chunk(chunk) for chunk in selected_chunks],
            budget.retrieved_memory_tokens,
        )
        note += "可能相关但不确定，只能谨慎参考，不能当作确定事实。"
    elif needs_retrieval:
        content = "没有检索到足够可靠的笔记。"
        note += "用户问题需要记忆，但当前没有可靠依据；回答时应诚实说明不确定。"
    else:
        content = "本轮未查询个人知识库。"
        note += "这是普通问答或闲聊，不要声称使用了用户笔记。"

    return ContextLayer(
        level=3,
        name="RAG 检索记忆",
        content=content,
        budget_tokens=budget.retrieved_memory_tokens,
        used_tokens=count_tokens(content),
        note=note,
    )


def build_summary_layer(
    conversation_summary: str | None,
    budget: ContextBudget,
) -> ContextLayer:
    """构建 L2 对话摘要层。"""

    summary = (conversation_summary or "").strip()
    if not summary:
        content = "暂无对话摘要。"
    else:
        content = _truncate_text_to_budget(summary, budget.summary_tokens)
    return ContextLayer(
        level=2,
        name="对话摘要",
        content=content,
        budget_tokens=budget.summary_tokens,
        used_tokens=count_tokens(content),
        note="承接较早对话；如果与近期消息冲突，以近期消息和当前输入为准。",
    )


def build_recent_messages_layer(
    recent_messages: list[dict[str, Any]],
    budget: ContextBudget,
) -> ContextLayer:
    """构建 L1 近期对话层。

    从最新消息向前装入，直到达到 token 预算，再恢复为时间正序。
    这样能优先保留离当前问题最近的上下文。
    """

    selected: list[str] = []
    used_tokens = 0
    for message in reversed(recent_messages):
        line = _format_message(message)
        line_tokens = _message_token_count(message, line)
        if not selected and line_tokens > budget.recent_message_tokens:
            selected.append(_truncate_text_to_budget(line, budget.recent_message_tokens))
            break
        if selected and used_tokens + line_tokens > budget.recent_message_tokens:
            break
        selected.append(line)
        used_tokens += line_tokens

    if not selected:
        content = "无近期对话。"
    else:
        content = "\n".join(reversed(selected))
    return ContextLayer(
        level=1,
        name="近期对话窗口",
        content=content,
        budget_tokens=budget.recent_message_tokens,
        used_tokens=count_tokens(content),
        note="最贴近当前会话状态；按 token 预算保留最近消息。",
    )


def build_current_conversation_window_layer(
    recent_messages: list[dict[str, Any]],
    user_message: str,
    budget: ContextBudget,
) -> ContextLayer:
    """构建 L1+L0 当前对话窗口层。

    这一层把近期消息和当前用户输入合并成一段连续对话，让 LLM 把它理解为
    “正在发生的一次对话”，而不是两个割裂的信息块。当前用户输入永远保留在最后；
    近期消息按预算从近到远保留。
    """

    current_line = _format_message({"role": "user(current)", "content": user_message})
    current_tokens = count_tokens(current_line)
    recent_budget = max(budget.conversation_window_tokens - current_tokens, 0)
    selected: list[str] = []
    used_tokens = 0
    for message in reversed(recent_messages):
        line = _format_message(message)
        line_tokens = _message_token_count(message, line)
        if recent_budget <= 0:
            break
        if not selected and line_tokens > recent_budget:
            selected.append(_truncate_text_to_budget(line, recent_budget))
            break
        if selected and used_tokens + line_tokens > recent_budget:
            break
        selected.append(line)
        used_tokens += line_tokens

    lines = [*reversed(selected), current_line]
    content = "\n".join(line for line in lines if line.strip())
    return ContextLayer(
        level=1,
        name="当前对话窗口",
        content=content,
        budget_tokens=budget.conversation_window_tokens,
        used_tokens=count_tokens(content),
        note="L1 近期消息和 L0 当前输入合并后的连续对话；工具规划和最终回答都应优先理解这一层。",
    )


def build_current_input_layer(user_message: str) -> ContextLayer:
    """构建 L0 当前输入层。"""

    content = user_message.strip()
    return ContextLayer(
        level=0,
        name="当前用户输入",
        content=content,
        budget_tokens=None,
        used_tokens=count_tokens(content),
        note="本轮必须直接回应的用户问题或指令。",
    )


def context_layer_from_payload(payload: dict[str, Any]) -> ContextLayer:
    """从 checkpoint payload 还原 ContextLayer。"""

    return ContextLayer(
        level=int(payload["level"]),
        name=str(payload["name"]),
        content=str(payload["content"]),
        budget_tokens=payload.get("budget_tokens"),
        used_tokens=int(payload["used_tokens"]),
        note=str(payload.get("note") or ""),
    )


def _format_chunk(chunk: dict[str, Any]) -> str:
    title = str(chunk.get("note_title") or "未命名笔记")
    score = chunk.get("score")
    score_text = f", score={float(score):.3f}" if score is not None else ""
    content = str(chunk.get("content") or "").strip()
    return f"- [{title}{score_text}] {content}"


def _format_message(message: dict[str, Any]) -> str:
    role = str(message.get("role") or "unknown")
    content = str(message.get("content") or "").strip()
    return f"{role}: {content}"


def _message_token_count(message: dict[str, Any], fallback_text: str) -> int:
    token_count = message.get("token_count")
    if isinstance(token_count, int) and token_count > 0:
        return token_count
    return count_tokens(fallback_text)


def _fit_lines_to_budget(lines: list[str], budget_tokens: int) -> str:
    """按行装入预算。

    chunk 和记忆都是天然的行级条目。整行裁剪比从中间硬截断更容易保留语义完整性。
    如果第一行就超预算，再对第一行做文本截断，保证该层至少有可用内容。
    """

    selected: list[str] = []
    used_tokens = 0
    for line in lines:
        line_tokens = count_tokens(line)
        if selected and used_tokens + line_tokens > budget_tokens:
            break
        if not selected and line_tokens > budget_tokens:
            return _truncate_text_to_budget(line, budget_tokens)
        selected.append(line)
        used_tokens += line_tokens
    return "\n".join(selected) if selected else "无。"


def _truncate_text_to_budget(text: str, budget_tokens: int) -> str:
    """把单段文本裁剪到预算内。

    当前 tokenizer 是估算实现，裁剪用字符比例逐步收缩，避免引入复杂依赖。
    后续如果切换 tiktoken 或 provider tokenizer，可以只替换这里。
    """

    normalized = text.strip()
    if count_tokens(normalized) <= budget_tokens:
        return normalized

    # 先用 token 预算和当前估算 token 的比例给出初始长度，再循环微调。
    ratio = max(0.1, budget_tokens / max(count_tokens(normalized), 1))
    candidate = normalized[: max(1, int(len(normalized) * ratio))]
    while candidate and count_tokens(candidate + "...") > budget_tokens:
        candidate = candidate[: max(1, int(len(candidate) * 0.85))]
    return candidate.rstrip() + "..."
