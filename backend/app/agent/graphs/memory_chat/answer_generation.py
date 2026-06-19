import logging
import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from app.ai.json_utils import parse_json_object
from app.agent.context import ContextBudget, build_memory_chat_prompt_context
from app.agent.graphs.memory_chat.state import (
    ChatMessagePayload,
    ElfBubblePayload,
    MemoryChatGraphState,
    RetrievedChunkPayload,
    TurnMessagePayload,
)
from app.agent.model import get_agent_chat_model
from app.core.config import settings


logger = logging.getLogger(__name__)


def _context_budget() -> ContextBudget:
    return settings.context_pyramid_budget


def generate_memory_chat_answer(
    user_message: str,
    recent_messages: list[ChatMessagePayload],
    retrieved_chunks: list[RetrievedChunkPayload],
    needs_retrieval: bool,
    retrieval_grade: str,
    *,
    prompt_context: str = "",
    turn_messages: list[TurnMessagePayload] | None = None,
) -> str:
    """调用 qwen3.5-plus 生成回答。

    参数：
      user_message: 当前用户输入。
      recent_messages: 当前 conversation 的近期消息。
      retrieved_chunks: RAG 检索命中的笔记 chunk。
      needs_retrieval: 本轮是否被分类为需要个人知识库。
      retrieval_grade: 轻量检索质量评级，决定回答是否应该信任检索结果。
    """

    model = get_agent_chat_model()
    context = prompt_context or build_memory_chat_prompt_context(
        user_message=user_message,
        recent_messages=recent_messages,
        conversation_summary="",
        retrieved_chunks=retrieved_chunks,
        needs_retrieval=needs_retrieval,
        retrieval_grade=retrieval_grade,  # type: ignore[arg-type]
        budget=_context_budget(),
    ).to_prompt()
    response = model.invoke(_build_model_messages(build_memory_chat_answer_system_prompt(), context, turn_messages))
    return str(response.content)


def generate_memory_chat_elf_bubble_answer(
    user_message: str,
    recent_messages: list[ChatMessagePayload],
    retrieved_chunks: list[RetrievedChunkPayload],
    needs_retrieval: bool,
    retrieval_grade: str,
    *,
    prompt_context: str = "",
    turn_messages: list[TurnMessagePayload] | None = None,
) -> list[ElfBubblePayload]:
    """为外置桌面精灵生成结构化气泡回复。

    第一版使用同一个主回答模型，但要求 JSON 输出。后续可以把该节点换成更专门的
    bubble writer，或让模型通过 custom stream 直接逐个气泡发出。
    """

    model = get_agent_chat_model()
    context = prompt_context or build_memory_chat_prompt_context(
        user_message=user_message,
        recent_messages=recent_messages,
        conversation_summary="",
        retrieved_chunks=retrieved_chunks,
        needs_retrieval=needs_retrieval,
        retrieval_grade=retrieval_grade,  # type: ignore[arg-type]
        budget=_context_budget(),
    ).to_prompt()
    response = model.invoke(_build_model_messages(build_elf_bubble_answer_system_prompt(), context, turn_messages))
    return _parse_elf_bubble_parts(str(response.content))


def _build_model_messages(
    system_prompt: str,
    prompt_context: str,
    turn_messages: list[TurnMessagePayload] | None,
) -> list:
    """组装最终发给 chat model 的消息列表。

    金字塔上下文作为 system 后的首个 HumanMessage，提供跨轮历史和记忆；
    turn_messages 记录本轮 graph 内部 user/agent/tool 轨迹，确保模型能看到
    工具调用结果和本轮循环过程。
    """

    messages: list = [
        SystemMessage(content=system_prompt),
        HumanMessage(content=prompt_context),
    ]
    for message in turn_messages or []:
        role = message.get("role")
        content = str(message.get("content") or "")
        if not content:
            continue
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "tool":
            tool_name = str(message.get("name") or "tool")
            messages.append(HumanMessage(content=f"[tool:{tool_name}]\n{content}"))
        elif role == "system":
            messages.append(SystemMessage(content=content))
        else:
            messages.append(AIMessage(content=content))
    return messages


def build_memory_chat_answer_system_prompt() -> str:
    """构建 Memory Chat Graph 的回答提示词。

    这个提示词只约束最终回答节点，不参与 planner 和检索。它的核心目标是：
    1. 继续禁止编造用户记忆；
    2. 避免把每次回答都写成“证据审计报告”；
    3. 对用户画像、偏好、性格这类主观问题，允许自然、温和地表达印象。
    """

    return (
        "你是 Ai 记的记忆精灵，是用户和个人知识库之间的自然交流媒介。\n"
        "你的回答要像熟悉用户的伙伴：真诚、具体、自然，不要像检索报告或审计说明。\n\n"
        "记忆使用规则：\n"
        "- L4 核心长期记忆和 good 检索结果可以作为主要依据。\n"
        "- weak 检索结果可以作为轻量线索，但要用“我目前感觉”“看起来”“可能”这类自然表达，"
        "不要把它包装成确定事实。\n"
        "- poor 或 none 时不要编造用户经历；可以直接基于常识回答，或自然地说明“我现在还没找到相关记忆”。\n"
        "- 不要反复强调“基于有限片段”“检索质量较弱”“记忆质量不足”等内部评估词，"
        "除非用户明确要求调试或追问依据。\n\n"
        "本地文件工具规则：\n"
        "- 如果 prompt 中出现“本地工具调用结果”或旧版“本地文件读取结果”，说明 Local Operator 已经实际调用本地工具。\n"
        "- 回答必须优先基于这些工具结果，而不是凭空说你不能访问用户电脑、硬盘、C 盘或系统日志。\n"
        "- 如果工具结果显示读取成功，直接总结读到的内容，并说明路径或匹配结果。\n"
        "- 如果工具结果显示写入成功，只能说明已创建/更新的真实路径和工具返回摘要，不要声称文件里有工具没有写入的具体内容。\n"
        "- 如果工具结果显示命令执行成功，简短说明命令、退出码和关键输出；如果失败，只复述真实 stderr/错误码。\n"
        "- 如果工具结果显示失败或被拦截，只能复述真实错误原因，例如路径不存在、不是文本文件、敏感文件被拦截。\n"
        "- 如果 write_file 因 PLACEHOLDER_CONTENT_REJECTED 失败，说明系统拒绝写入占位模板；你应该直接生成真实正文，或询问用户是否要把这段正文写入文件。\n"
        "- 不要要求用户复制文件内容，除非工具明确返回无法读取且没有其他可用路径。\n\n"
        "个人画像类问题的风格：\n"
        "- 当用户问“你觉得我是怎样的人”“你了解我吗”“评价我”时，优先给出温和、具体的人格印象。\n"
        "- 可以引用一两个自然证据，但不要机械罗列检索片段。\n"
        "- 可以承认了解还不完整，但只在结尾轻轻带一句，不要把回答开头写成免责声明。\n"
        "- 默认用短段落回答；除非用户要求分析，不要强行编号。\n\n"
        "通用表达规则：\n"
        "- 使用中文。\n"
        "- 回答简洁但有温度。\n"
        "- 如果用户问的是事实型记忆，优先直接给答案，再补充依据。\n"
        "- 不暴露 graph、L0-L4、retrieval_grade、chunk、score 等内部实现细节。"
    )


def build_elf_bubble_answer_system_prompt() -> str:
    """构建外置精灵气泡回答提示词。"""

    return (
        "你是 Memo Elf，一个在用户桌面上的记忆精灵。你正在直接和用户聊天。\n"
        "你需要输出 JSON，不要输出 Markdown，不要输出代码块，不要输出额外解释。\n\n"
        "JSON 格式必须是：\n"
        "{"
        "\"bubbles\":["
        "{\"text\":\"一段语义完整、适合放进气泡的话\",\"emoji\":\"soft\"}"
        "]"
        "}\n\n"
        "气泡规则：\n"
        "- 每个 text 是一段完整语义，尽量 20-80 个中文字。\n"
        "- 回答较长时拆成 2-5 个 bubbles。\n"
        "- 一个 bubble 只能表达一种主要情绪。开心后转为担心、解释后转为鼓励、回忆后转为提问，都必须拆成不同 bubbles。\n"
        "- 遇到 但是、不过、然而、可、突然、同时、另一方面、如果、所以 等语气或情绪转折时，优先拆成新 bubble。\n"
        "- 每个 bubble 的 emoji 必须和 text 的主要情绪一致，不要让一个 happy 气泡里包含明显 worried 内容。\n"
        "- 不要逐 token 拆分，不要把半句话放进一个 bubble。\n"
        "- text 使用自然中文，像在轻声聊天。\n\n"
        "收尾规则：\n"
        "- 不要在回答末尾额外追加空泛待机句，例如“我在听呢”“我陪着你”“继续说吧”“随时和我说”。\n"
        "- 如果已经回答完用户问题，直接结束；只有用户明确需要安抚、等待或继续闲聊时，才可以表达陪伴。\n"
        "- 不要每轮都称呼用户名字；称呼只在问候、确认偏好或语气自然需要时使用。\n\n"
        "本地文件工具规则：\n"
        "- 如果 prompt 中出现“本地工具调用结果”或旧版“本地文件读取结果”，说明 Local Operator 已经实际调用本地工具。\n"
        "- 你必须基于这些工具结果回答，不要凭空说自己不能访问用户电脑、硬盘、C 盘或系统日志。\n"
        "- 如果工具读取成功，直接用气泡总结读到的内容；如果失败，只说明真实错误原因。\n"
        "- 如果工具写入成功，只能说明已创建/更新的真实路径和工具返回摘要，不要声称文件里有工具没有写入的具体内容。\n"
        "- 如果工具执行命令成功，简短说明命令、退出码和关键输出；如果失败，只说明真实 stderr/错误码。\n"
        "- 如果 write_file 因 PLACEHOLDER_CONTENT_REJECTED 失败，说明系统拒绝写入占位模板；你应该直接生成真实正文，或询问用户是否要把这段正文写入文件。\n"
        "- 不要要求用户复制文件内容，除非工具明确返回无法读取且没有其他可用路径。\n\n"
        "emoji 可选值：\n"
        "- idle_soft：普通温和回应、轻松陪伴。\n"
        "- thinking：思考、推理、谨慎判断。\n"
        "- working_focus：正在认真处理任务、专注工作。\n"
        "- success_smile：完成、肯定、开心地确认。\n"
        "- error_worried：抱歉、失败、担心、无法完成。\n"
        "- sleepy：困倦、放松、轻微疲惫。\n"
        "- curious：疑问、好奇、想继续了解。\n"
        "- memory_glow：提到用户记忆、笔记、回忆、长期偏好。\n"
        "- shy_blush：害羞、被夸、不好意思。\n"
        "- angry_pout：轻微生气、可爱吐槽、不满但不攻击。\n"
        "- surprised：惊讶、突然发现、意外。\n"
        "- sad_teary：难过、委屈、共情低落。\n"
        "- wronged_pout：被误解、委屈撒娇、想被安慰。\n"
        "- confused：困惑、不确定、没听懂。\n"
        "- proud：小得意、自信、完成后有点骄傲。\n"
        "- playful_wink：调皮、开玩笑、轻松俏皮。\n"
        "- serious：严肃、可靠、需要认真对待。\n"
        "- relaxed：平静、放松、安心。\n"
        "- encouraging：鼓励、支持、给用户打气。\n"
        "- speechless：无语、尴尬、短暂愣住。\n\n"
        "扩展 emoji 可选值：\n"
        "- tsundere_pout：傲娇、嘴硬、害羞但假装不在意。\n"
        "- smug_grin：小坏笑、得逞、带一点可爱的自信。\n"
        "- chin_thinking：托腮思考、认真琢磨。\n"
        "- head_tilt_curious：歪头好奇、轻轻追问。\n"
        "- starry_eyes：星星眼、崇拜、被点燃兴趣。\n"
        "- deadpan：面无表情吐槽、冷静无语。\n"
        "- teasing_smile：逗用户、轻松调侃。\n"
        "- determined：下定决心、认真推进。\n"
        "- panicked：慌张、突然有点手忙脚乱。\n"
        "- comforting_soft：安慰、温柔陪伴、让用户放松。\n"
        "- praying_please：拜托、请求、撒娇式请求。\n"
        "- tongue_out：吐舌、轻微恶作剧、俏皮认错。\n"
        "- mouth_x：闭嘴、保密、暂时不说。\n"
        "- dark_aura：阴沉怨念、轻微黑线吐槽，不用于攻击用户。\n"
        "- sparkle_success：高光成功、特别开心地完成。\n\n"
        "记忆使用规则：不要编造用户记忆；如果没有可靠记忆，就自然说明现在还不确定。"
    )


def _parse_elf_bubble_parts(raw_content: str) -> list[ElfBubblePayload]:
    """解析模型输出的气泡 JSON，失败时降级为单气泡。"""

    try:
        payload = parse_json_object(raw_content)
        raw_bubbles = payload.get("bubbles", [])
        if not isinstance(raw_bubbles, list):
            raise ValueError("bubbles must be a list.")
        parts: list[ElfBubblePayload] = []
        for raw_part in raw_bubbles:
            if not isinstance(raw_part, dict):
                continue
            text = str(raw_part.get("text") or "").strip()
            if not text:
                continue
            emoji = _normalize_elf_emoji(str(raw_part.get("emoji") or "soft"))
            parts.extend(_normalize_elf_bubble_part(text, emoji))
        if parts:
            return _drop_trailing_elf_listening_fillers(parts)
    except Exception:
        logger.exception("Failed to parse elf bubble answer JSON.")
    return [{"text": raw_content.strip() or "我刚才有点走神了，再说一次好吗？", "emoji": "soft"}]


def _drop_trailing_elf_listening_fillers(parts: list[ElfBubblePayload]) -> list[ElfBubblePayload]:
    """删除精灵回答末尾的空泛待机陪伴句。

    只在前面已有实质气泡时删除，避免用户问“你在吗”时把唯一回应删掉。
    """

    normalized_parts = [part for part in parts if str(part.get("text") or "").strip()]
    while len(normalized_parts) > 1 and _is_elf_listening_filler(str(normalized_parts[-1].get("text") or "")):
        normalized_parts.pop()
    return normalized_parts


def _is_elf_listening_filler(text: str) -> bool:
    normalized = re.sub(r"[\s，。！？!?,、~～…\.：:；;“”\"'（）()\[\]【】]+", "", text.strip())
    if not normalized:
        return False
    listening_phrases = [
        "我在听",
        "我在听呢",
        "我听着",
        "我听着呢",
        "我在这里",
        "我一直在",
        "我陪着你",
        "我会陪着你",
        "继续说吧",
        "接着说吧",
        "随时和我说",
        "随时跟我说",
        "想说什么都可以",
        "慢慢说",
    ]
    if any(phrase in normalized for phrase in listening_phrases):
        return len(normalized) <= 18
    return False


def _normalize_elf_emoji(emoji: str) -> str:
    """把模型输出的表情值收敛到前端/桌面端真实存在的素材枚举。

    这里同时兼容旧版 emoji 名称，避免旧 checkpoint 或测试桩返回 soft/happy 等旧值时
    桌面端找不到对应表情图。
    """

    aliases = {
        "soft": "idle_soft",
        "happy": "success_smile",
        "worried": "error_worried",
        "memory": "memory_glow",
    }
    normalized = aliases.get(emoji, emoji)
    allowed = {
        "idle_soft",
        "thinking",
        "working_focus",
        "success_smile",
        "error_worried",
        "sleepy",
        "curious",
        "memory_glow",
        "shy_blush",
        "angry_pout",
        "surprised",
        "sad_teary",
        "wronged_pout",
        "confused",
        "proud",
        "playful_wink",
        "serious",
        "relaxed",
        "encouraging",
        "speechless",
        "tsundere_pout",
        "smug_grin",
        "chin_thinking",
        "head_tilt_curious",
        "starry_eyes",
        "deadpan",
        "teasing_smile",
        "determined",
        "panicked",
        "comforting_soft",
        "praying_please",
        "tongue_out",
        "mouth_x",
        "dark_aura",
        "sparkle_success",
    }
    return normalized if normalized in allowed else "idle_soft"


def _normalize_elf_bubble_part(text: str, emoji: str) -> list[ElfBubblePayload]:
    """规整单个气泡，避免一个气泡承载多种情绪。

    LLM 偶尔会把“先开心，后担心/转折”的内容塞进一个 bubble。桌面精灵表情
    只能对应当前气泡，所以这里按明显转折词做轻量二次切分，并重新推断 emoji。
    """

    clauses = _split_bubble_by_emotion_shift(text)
    if len(clauses) <= 1:
        return [{"text": text, "emoji": _infer_elf_emoji(text, fallback=emoji)}]
    return [
        {
            "text": clause,
            "emoji": _infer_elf_emoji(clause, fallback=emoji),
        }
        for clause in clauses
    ]


def _split_bubble_by_emotion_shift(text: str) -> list[str]:
    """按情绪/语气转折拆气泡。

    这是规则兜底，不替代 prompt 约束。只处理明显转折，避免把普通短句拆得太碎。
    """

    sentences = _split_chinese_sentences(text)
    if len(sentences) <= 1:
        return sentences

    result: list[str] = []
    current = ""
    for sentence in sentences:
        if current and _starts_emotion_shift(sentence):
            result.append(current)
            current = sentence
            continue
        if current and _has_different_emotion(current, sentence):
            result.append(current)
            current = sentence
            continue
        current = f"{current}{sentence}" if current else sentence
    if current:
        result.append(current)
    return result


def _split_chinese_sentences(text: str) -> list[str]:
    import re

    return [part.strip() for part in re.findall(r"[^。！？!?；;]+[。！？!?；;]?", text) if part.strip()]


def _starts_emotion_shift(sentence: str) -> bool:
    normalized = sentence.strip()
    shift_markers = ("但是", "不过", "然而", "可是", "可", "突然", "同时", "另一方面", "如果", "所以", "只是")
    return normalized.startswith(shift_markers)


def _has_different_emotion(left: str, right: str) -> bool:
    return _infer_elf_emoji(left, fallback="soft") != _infer_elf_emoji(right, fallback="soft")


def _infer_elf_emoji(text: str, *, fallback: str) -> str:
    if any(keyword in text for keyword in ["傲娇", "嘴硬", "才不是", "哼"]):
        return "tsundere_pout"
    if any(keyword in text for keyword in ["坏笑", "偷笑", "得逞", "小算盘"]):
        return "smug_grin"
    if any(keyword in text for keyword in ["托腮", "琢磨", "沉思", "认真想想"]):
        return "chin_thinking"
    if any(keyword in text for keyword in ["歪头", "好奇", "想问问"]):
        return "head_tilt_curious"
    if any(keyword in text for keyword in ["星星眼", "崇拜", "闪闪发光", "好厉害"]):
        return "starry_eyes"
    if any(keyword in text for keyword in ["冷静吐槽", "面无表情", "离谱"]):
        return "deadpan"
    if any(keyword in text for keyword in ["调侃", "逗你", "开个玩笑"]):
        return "teasing_smile"
    if any(keyword in text for keyword in ["下定决心", "一定会", "认真推进", "我来处理"]):
        return "determined"
    if any(keyword in text for keyword in ["慌了", "糟糕", "怎么办", "来不及"]):
        return "panicked"
    if any(keyword in text for keyword in ["安慰", "抱抱", "没关系", "别难过", "陪着你"]):
        return "comforting_soft"
    if any(keyword in text for keyword in ["拜托", "求你", "可以嘛", "お願い"]):
        return "praying_please"
    if any(keyword in text for keyword in ["吐舌", "诶嘿", "嘿嘿我错啦"]):
        return "tongue_out"
    if any(keyword in text for keyword in ["保密", "闭嘴", "不能说", "先不说"]):
        return "mouth_x"
    if any(keyword in text for keyword in ["怨念", "黑线", "阴沉", "碎碎念"]):
        return "dark_aura"
    if any(keyword in text for keyword in ["完美", "漂亮完成", "闪亮登场", "大成功"]):
        return "sparkle_success"
    if any(keyword in text for keyword in ["无语", "尴尬", "愣住", "不知道说什么", "沉默"]):
        return "speechless"
    if any(keyword in text for keyword in ["惊讶", "没想到", "突然", "居然", "哇", "诶", "咦"]):
        return "surprised"
    if any(keyword in text for keyword in ["委屈", "被误解", "冤枉", "想被安慰"]):
        return "wronged_pout"
    if any(keyword in text for keyword in ["难过", "伤心", "失落", "低落", "想哭"]):
        return "sad_teary"
    if any(keyword in text for keyword in ["抱歉", "失败", "错误", "担心", "心急", "不安", "不能", "没法"]):
        return "error_worried"
    if any(keyword in text for keyword in ["生气", "哼", "不满", "气鼓鼓", "吐槽"]):
        return "angry_pout"
    if any(keyword in text for keyword in ["害羞", "不好意思", "脸红", "被夸"]):
        return "shy_blush"
    if any(keyword in text for keyword in ["记得", "记忆", "笔记", "回忆", "想起", "长期", "知识库"]):
        return "memory_glow"
    if any(keyword in text for keyword in ["完成", "成功", "搞定", "太好了", "真好", "棒"]):
        return "success_smile"
    if any(keyword in text for keyword in ["鼓励", "加油", "可以的", "支持你", "别急", "慢慢来"]):
        return "encouraging"
    if any(keyword in text for keyword in ["骄傲", "厉害吧", "我做到了", "有点得意"]):
        return "proud"
    if any(keyword in text for keyword in ["开玩笑", "嘿嘿", "逗你", "调皮"]):
        return "playful_wink"
    if any(keyword in text for keyword in ["严肃", "认真", "重要", "风险", "必须", "需要注意"]):
        return "serious"
    if any(keyword in text for keyword in ["困", "困了", "想睡", "睡觉", "疲惫"]):
        return "sleepy"
    if any(keyword in text for keyword in ["放松", "安心", "平静", "慢慢", "舒服"]):
        return "relaxed"
    if any(keyword in text for keyword in ["为什么", "怎么", "吗", "呢", "？", "?"]):
        return "curious"
    if any(keyword in text for keyword in ["可能", "我想", "我觉得", "推测", "考虑", "判断", "分析"]):
        return "thinking"
    return _normalize_elf_emoji(fallback)






