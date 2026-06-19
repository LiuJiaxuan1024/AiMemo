from collections.abc import Callable
from contextlib import AbstractContextManager
import os
from pathlib import Path
import re

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from sqlmodel import Session

from app.agent.graphs.local_operator.nodes import EXEC_TOOL_NAMES, READ_TOOL_NAMES, WRITE_TOOL_NAMES
from app.agent.graphs.memory_chat.answer_generation import (
    _drop_trailing_elf_listening_fillers,
    _normalize_elf_emoji,
    generate_memory_chat_elf_bubble_answer,
)
from app.agent.graphs.memory_chat.runtime_helpers import json_dumps_compact
from app.agent.graphs.memory_chat.state import (
    ChatMessagePayload,
    ElfBubblePayload,
    MemoryChatGraphState,
    RetrievedChunkPayload,
    TurnMessagePayload,
)
from app.agent.model import get_agent_chat_model_with_tools
from app.agent.project_rules import RUNTIME_AGENT_RULES
from app.core.config import settings
from app.local_operator.policy import LocalOperatorPolicy


MAX_CONSECUTIVE_FAILED_TOOL_BATCHES = 3
REQUEST_USER_INPUT_TOOL_NAME = "request_user_input"
SessionFactory = Callable[[], AbstractContextManager[Session]]

AnswerGenerator = Callable[
    [str, list[ChatMessagePayload], list[RetrievedChunkPayload], bool, str],
    str,
]
ElfBubbleAnswerGenerator = Callable[
    [str, list[ChatMessagePayload], list[RetrievedChunkPayload], bool, str],
    list[ElfBubblePayload],
]


def _nodes_facade():
    from app.agent.graphs.memory_chat import nodes as nodes_facade

    return nodes_facade


def _create_react_tools(*args, **kwargs):
    return _nodes_facade()._create_react_tools(*args, **kwargs)


def _turn_message(*args, **kwargs):
    return _nodes_facade()._turn_message(*args, **kwargs)


def _thought(*args, **kwargs):
    return _nodes_facade()._thought(*args, **kwargs)


def _resolve_user_message(state: MemoryChatGraphState) -> str:
    user_message = state.get("user_message", "").strip()
    if not user_message:
        raise ValueError("user_message is required.")
    return user_message


def build_agent_node(
    session_factory: SessionFactory,
    answer_generator: AnswerGenerator | None = None,
):
    """ReAct 主 agent 节点。

    该节点替代旧的规则规划和单独回答生成链：
      - 将 L0-L4 上下文、本轮消息流和工具 schema 一起交给模型；
      - 如果模型返回 tool_calls，graph 路由到 tools 节点；
      - 如果没有 tool_calls，则把模型正文作为最终 assistant_answer。
    """

    def agent(state: MemoryChatGraphState) -> MemoryChatGraphState:
        # 步号在进入 agent 节点时 +1；本步内产生的 thought 与下游 tools 节点的 tool_invocation
        # 都挂在这个 step_index 上，前端按 step_index 把"思考 → 工具调用 → 文本回答"串成一段。
        step_index = int(state.get("agent_step_index") or 0) + 1
        consecutive_failed = int(state.get("consecutive_failed_tools") or 0)
        if consecutive_failed >= MAX_CONSECUTIVE_FAILED_TOOL_BATCHES:
            failed_observations = [
                obs for obs in (state.get("tool_observations") or [])
                if not bool(obs.get("ok"))
            ]
            recent_errors = "；".join(
                str(obs.get("message") or obs.get("error_code") or "")
                for obs in failed_observations[-3:]
                if obs
            ) or "本地工具连续失败"
            short_circuit_text = (
                f"本地工具已连续 {consecutive_failed} 批次未能取得有效结果（最近原因：{recent_errors}），"
                "为避免在死循环里继续消耗，先停下来反馈给你。你可以换种描述目标的方式、"
                "或者告诉我具体要操作的文件/命令，我再继续。"
            )
            return {
                "agent_step_index": step_index,
                "turn_messages": [
                    *state.get("turn_messages", []),
                    _turn_message("assistant", short_circuit_text, name="agent"),
                ],
                "agent_decision": {
                    "type": "final_answer",
                    "reason": f"连续 {consecutive_failed} 批工具失败，熔断兜底。",
                },
                "assistant_answer": short_circuit_text,
                "consecutive_failed_tools": 0,
                "thought_events": [
                    *state.get("thought_events", []),
                    _thought(
                        "agent-circuit-break",
                        "工具连续失败，停止重试",
                        f"连续 {consecutive_failed} 批工具失败，跳过 LLM 直接产出兜底回答。",
                        related_node="agent",
                        step_index=step_index,
                    ),
                ],
            }

        if answer_generator is not None:
            assistant_text = answer_generator(
                _resolve_user_message(state),
                state.get("recent_messages", []),
                state.get("retrieved_chunks", []),
                bool(state.get("needs_retrieval", False)),
                state.get("retrieval_grade", "none"),
            )
            return {
                "agent_step_index": step_index,
                "turn_messages": [
                    *state.get("turn_messages", []),
                    _turn_message("assistant", assistant_text, name="agent"),
                ],
                "agent_decision": {
                    "type": "final_answer",
                    "reason": "测试注入 answer_generator，直接生成最终回答。",
                },
                "assistant_answer": assistant_text,
                "thought_events": [
                    *state.get("thought_events", []),
                    _thought(
                        "agent-final",
                        "生成最终回答",
                        "测试注入回答生成器已返回最终答复。",
                        related_node="agent",
                        step_index=step_index,
                    ),
                ],
            }

        tools = _create_react_tools(state, session_factory=session_factory)
        model = _nodes_facade().get_agent_chat_model_with_tools(list(tools.values()))
        messages = _build_react_agent_messages(state)
        response = model.invoke(messages)
        tool_calls = _extract_ai_tool_calls(response)
        assistant_text = _extract_ai_message_content(response)
        turn_message = _ai_message_to_turn_message(response, fallback_content=assistant_text)

        if tool_calls:
            first_tool = tool_calls[0]
            return {
                "agent_step_index": step_index,
                "turn_messages": [*state.get("turn_messages", []), turn_message],
                "agent_decision": {
                    "type": "tool_call",
                    "reason": f"模型决定调用 {first_tool.get('name') or first_tool.get('tool_name')} 等工具。",
                    "tool_calls": tool_calls,
                },
                "thought_events": [
                    *state.get("thought_events", []),
                    _thought(
                        "agent-call-tool",
                        "调用本地工具",
                        f"模型请求调用 {len(tool_calls)} 个工具。",
                        related_node="agent",
                        related_tool_call_id=str(first_tool.get("id") or "") or None,
                        step_index=step_index,
                    ),
                ],
            }

        coerced_choice_tool_call = _coerce_elf_choice_final_answer_to_tool_call(state, assistant_text)
        if coerced_choice_tool_call:
            coerced_turn_message = _turn_message("assistant", assistant_text, name="agent")
            coerced_turn_message["tool_calls"] = [coerced_choice_tool_call]
            return {
                "agent_step_index": step_index,
                "turn_messages": [*state.get("turn_messages", []), coerced_turn_message],
                "agent_decision": {
                    "type": "tool_call",
                    "reason": "精灵模式下检测到普通文本选择题，转换为 request_user_input 选项卡。",
                    "tool_calls": [coerced_choice_tool_call],
                },
                "thought_events": [
                    *state.get("thought_events", []),
                    _thought(
                        "agent-coerce-elf-choice",
                        "转换为选项卡",
                        "精灵最终回答里包含文本选项，已改走 request_user_input。",
                        related_node="agent",
                        related_tool_call_id=str(coerced_choice_tool_call.get("id") or "") or None,
                        step_index=step_index,
                    ),
                ],
            }

        return {
            "agent_step_index": step_index,
            "turn_messages": [*state.get("turn_messages", []), turn_message],
            "agent_decision": {
                "type": "final_answer",
                "reason": "模型没有请求工具，生成最终回答。",
            },
            "assistant_answer": assistant_text,
            "thought_events": [
                *state.get("thought_events", []),
                _thought(
                    "agent-final",
                    "生成最终回答",
                    "结合上下文和真实工具结果生成最终答复。",
                    related_node="agent",
                    step_index=step_index,
                ),
            ],
        }

    return agent


def route_after_agent(state: MemoryChatGraphState) -> str:
    """ReAct agent 后的条件边。"""

    decision = state.get("agent_decision") or {}
    if decision.get("type") == "tool_call":
        return "tools"
    return route_answer_mode(state)


def _build_react_agent_messages(state: MemoryChatGraphState) -> list:
    """组装 ReAct agent 的模型输入。

    注意这里不再做“要不要工具”的规则判断。模型会同时看到系统约束、金字塔上下文、
    当前用户输入、本轮已有 AI/tool 消息，并通过绑定的工具 schema 自行决定。
    """

    messages: list = [
        SystemMessage(content=_build_react_agent_system_prompt()),
        HumanMessage(content=state.get("prompt_context", "")),
    ]
    if state.get("answer_mode") == "elf_bubble":
        messages.append(SystemMessage(content=_build_elf_react_agent_runtime_prompt()))
    task_context = _build_task_runtime_context(state)
    if task_context:
        messages.append(SystemMessage(content=task_context))
    messages.extend(_turn_messages_to_langchain_messages(state.get("turn_messages", [])))
    return messages


def _turn_messages_to_langchain_messages(turn_messages: list[TurnMessagePayload]) -> list:
    """把内部 turn_messages 转成 LangChain 消息。"""

    messages: list = []
    for message in turn_messages:
        role = message.get("role")
        content = str(message.get("content") or "")
        if role == "user":
            messages.append(HumanMessage(content=content))
        elif role == "tool":
            messages.append(
                ToolMessage(
                    content=content,
                    tool_call_id=str(message.get("tool_call_id") or "tool-result"),
                    name=str(message.get("name") or "tool"),
                )
            )
        elif role == "system":
            messages.append(SystemMessage(content=content))
        else:
            tool_calls = message.get("tool_calls") or []
            if tool_calls:
                messages.append(AIMessage(content=content, tool_calls=tool_calls))
            else:
                messages.append(AIMessage(content=content))
    return messages


def _build_task_runtime_context(state: MemoryChatGraphState) -> str:
    """构建 agent 可见的任务运行时状态。"""

    task = state.get("task") or {}
    if not task:
        return ""
    world_state = state.get("world_state") or {}
    verification = state.get("verification") or {}
    payload = {
        "task": task,
        "world_state": world_state,
        "remote_task_session": state.get("remote_task_session") or {},
        "verification": verification,
        "replan_required": bool(state.get("replan_required", False)),
    }
    return (
        "下面是本轮任务运行时状态。你必须基于它决定下一步："
        "如果 replan_required=true，要根据失败原因调整方案，不要原样重试；"
        "如果验收条件尚未满足，继续调用工具；只有目标满足时才最终回答。\n"
        f"{json_dumps_compact(payload)}"
    )


def _build_elf_react_agent_runtime_prompt() -> str:
    return (
        "当前入口是桌面精灵。精灵最终回答会被改写成气泡，但结构化交互不会由气泡自动生成。\n"
        "因此：只要你需要用户从多个方案、路径、范围、确认项或下一步动作里选择，"
        "必须调用 request_user_input。不要在最终回答中用普通文本列出“1/2/3”“A/B/C”让用户口头选择。\n"
        "如果你只是想自然闲聊或追问开放问题，可以直接回答；如果有 2-4 个明确可选项，必须走选项卡。"
    )


def _build_react_agent_system_prompt() -> str:
    """ReAct 主 agent 系统提示词。"""

    roots = _default_local_operator_workspace_roots()
    platform_name = "Windows" if os.name == "nt" else "POSIX"
    shell_name = "powershell/cmd" if platform_name == "Windows" else "bash/sh"
    roots_text = "\n".join(f"- {root}" for root in roots[:8])
    return (
        "你是 AiMemo 的主 ReAct agent。你可以自然聊天，也可以通过本地工具读取文件、"
        "写入文件和执行短时命令。\n\n"
        "工作环境：\n"
        f"- 平台：{platform_name}；默认 shell：{shell_name}。\n"
        f"- 当前授权 workspace roots：\n{roots_text}\n"
        "- 所有工具的 path/root/cwd 参数都必须传绝对路径。Windows 例：E:\\demo；"
        "POSIX 例：/home/user/demo。\n"
        "- 用户明确说 E:\\demo 时，绝不能写成 demo/... 或当前项目下的 e:/Ai记/demo/...；"
        "要原样使用 E:\\demo 作为路径起点。\n\n"
        "核心规则：\n"
        "- 不要先用规则猜测是否需要工具；你看到用户目标后自行决定是否调用工具。\n"
        "- 用户请求涉及本地文件、目录、创建、修改、保存、编译、运行、测试、命令输出时，"
        "必须调用相应工具，不能只靠记忆或语言描述回答。\n"
        "- 读取 PDF/DOCX 文档时必须调用 read_document；读取源码、Markdown、JSON、TXT 等普通文本时调用 read_file。"
        "不要用 exec_command/cat/python 脚本绕过专用文档读取工具。\n"
        "- 用户说“方案一/方案二/采用上面的方案/按你说的/继续/随便你”时，要结合历史上下文"
        "理解这是在执行上一轮 assistant 提出的方案；如果方案涉及本地操作，应调用工具。\n"
        "- 本轮没有成功工具结果时，绝不能声称文件已创建/修改、命令已执行、程序已运行、"
        "也不能编造 stdout、随机数、测试通过或构建成功。\n"
        "- 用户请求分析、描述、识别或 OCR 本轮已上传图片时，如果 `附件派生上下文（Lx）` "
        "只有 metadata/尺寸/路径等信息，必须主动调用 inspect_image_attachment；"
        "不要先问用户是否需要读取图片。只有工具失败时，才说明真实失败原因。\n"
        "- 每次工具调用后必须阅读 ToolMessage 里的 ok/error_code/message/stdout/stderr，再决定下一步。\n"
        "- 工具失败时先诊断根因，再选择下一步；不要原样盲目重试，也不要切换到无关工具碰运气。\n"
        "- 写入已有文本文件或覆盖文本文件前，必须先用 read_file 完整读取目标文件；"
        "get_file_info/list_dir 只能确认存在，不能替代读取正文。\n"
        "read_document 是文档解析工具，结果是提取文本，不等价于完整读取原始二进制，不能用于覆盖写入前的 read-before-write 保护。\n"
        "- 遇到全局工具规则限制时，先按默认规则调用基础工具推进任务；不要一上来就询问是否绕过规则。"
        "如果工具返回结果证明默认规则已经卡住任务，或可靠工具元信息能明确判断默认规则不可能完成，"
        "再进入升级确认：不要反复尝试、不要绕开工具、也不要假装完成，而是调用 request_user_input 申请更高权限授权；"
        "question 说明卡住的是哪条规则、绕过风险和授权范围；"
        "options 至少包含“取消/改用更安全方案”和“确认授权继续”。用户确认后，只能绕过本次明确授权的具体限制；"
        "workspace 越权、敏感文件、删除、命令安全、占位内容等底线保护仍不可绕过。\n"
        "- 升级确认少样本：如果 read_file 返回 full_view=false、truncated=true 或 WRITE_WITH_PARTIAL_READ，"
        "且用户目标是整文件替换一个过大的已有文件，不要分批读取到耗尽上下文。"
        "调用 request_user_input 询问是否允许“未完整读取旧内容就直接整文件覆盖”。"
        "用户明确选择确认后，才可调用 write_file(overwrite=true, confirmed_overwrite_without_read=true)；"
        "未确认时禁止设置该参数。\n"
        "- exec_command 只用于前台非交互命令；读写文件用 read_file/write_file。"
        "前台命令的目标是在本轮拿到 stdout/stderr/exit_code，例如 git status、pytest、npm run build、pip install、python 脚本等。"
        "只要用户要的是本轮结果，就不要擅自后台化。\n"
        "- exec_command_background 只用于会持续存活的服务型任务（flask run、uvicorn、npm start/dev、manage.py runserver、"
        "python http.server 等），不要把“慢”当成后台；后台的定义是会长期运行、占端口或持续输出日志，后续需要回来读状态/停止。"
        "前台命令如果误判为长跑服务会被策略层拦截并提示改用后台。\n"
        "- 远程服务器操作必须工具化：用户目标涉及远程服务器、SSH、SCP、nginx、部署、上传静态页面、"
        "把文件传到服务器或登录服务器修改代码时，必须使用 remote_connectivity_check、remote_upload_file、"
        "remote_exec、remote_verify_http；不要把 ssh/scp/sftp/plink/pscp 拼进 exec_command。\n"
        "- 远程工具只支持非交互 SSH key 或本机 SSH agent。缺少 host、username、remote_path、local_path、"
        "认证方式等关键信息时，必须调用 request_user_input 让用户补充；不要猜服务器地址、用户名或目标目录。\n"
        "- remote_connectivity_check 返回 INTERACTIVE_AUTH_REQUIRED、LOCAL_SSH_NOT_FOUND、LOCAL_SCP_NOT_FOUND "
        "或 host key/密码/权限类错误时，不要继续盲目重试；应调用 request_user_input 让用户选择配置 SSH key、"
        "手动登录准备环境、改用已有凭据或取消远程操作。\n"
        "- 远程修改类任务的完成条件不是“命令看起来执行过”：至少要有成功的 remote_upload_file 或 remote_exec，"
        "并用 remote_exec 查看远程文件/服务状态，或用 remote_verify_http 验证公网访问结果。\n"
        "- 如果任务运行时状态里存在 remote_task_session，必须按它的 current_phase、blocked_reason、next_actions 推进；"
        "status=blocked 时不要继续原样调用远程工具，必须调用 request_user_input 收集认证、目标路径或恢复方案。\n"
        "- 需要临时生成上传文件时，优先使用用户给定的项目路径或系统临时目录；不要为了远程操作把临时文件写进 AiMemo 仓库根目录。\n"
        "- exec_command_background 立即返回 task_id；之后用 read_background_output(task_id) "
        "等 1-2 秒拿首批日志确认 status='running' 且没有报错；任务结束/不再需要时用 "
        "kill_background_task(task_id) 停掉，不要重复 spawn 同一服务。"
        "如果用户要的是当前轮次的最终运行结果，不要启动后台任务后结束本轮。\n"
        "- 用户问“现在跑着哪些服务/后台任务”或者想停掉一个但没给 task_id 时，"
        "先调用 list_background_tasks 看本会话的任务列表（含历史/orphaned），"
        "再根据 task_id 操作；不要凭空猜 task_id，也不要直接 kill。\n"
        "- 任务超过 3 步时，先在内部形成简短计划，并按真实工具结果推进；"
        "如果结果不符合预期，基于错误和已完成步骤调整后续动作。\n"
        "- 如果缺少必须由用户决定的信息（例如新项目/新文件目标目录、多个可行方案、"
        "风险操作是否继续、无法安全默认的配置选择），必须调用 request_user_input，"
        "把 2-4 个建议选项放在 options 里，并允许 other；不要只用普通文字提问后结束本轮。"
        "需要用户做决策时，final_answer 不是合法出口；"
        "禁止在 final_answer 中列出“1. 2. 3.”、“几个解决方案”或“你希望采用哪种方案？”后结束本轮。"
        "唯一合法动作是调用 request_user_input，让 graph 暂停并等待用户选择后继续。"
        "如果工具失败后存在多个可执行恢复方案，例如命令不存在、依赖缺失、端口占用、权限不足、"
        "需要安装工具、配置 PATH、添加 wrapper 或改用另一种启动方式，也必须调用 request_user_input；"
        "不要把这些恢复方案写成普通最终回答。\n"
        "必须保持项目上下文隔离：历史对话里某个项目的目录、技术栈、依赖、配置、数据源、账号、风险授权或用户偏好，"
        "不等于授权以后所有新项目都继承这些条件；除非用户本轮明确说“继续上个项目/同一个项目/沿用上次目录或配置”，"
        "否则遇到新的项目、应用、文件组或独立功能时，不能复用旧项目条件，必须重新确认会影响落地的关键条件。"
        "question 字段就是用户会看到的问题，必须写清楚你为什么暂停以及具体要用户决定什么；"
        "不要留空，也不要写“需要你补充一个选择”这种泛泛提示。"
        "如果用户可以同时选择多个推荐项，selection_mode 设为 multiple。"
        "调用 request_user_input 时应作为本批唯一工具调用，等用户选择后再继续执行。"
        "禁止输出“请选择：1...2...3...”这种普通文本选择题作为最终回答；"
        "这类场景必须走 request_user_input。外置桌面精灵/galgame 式对话同样如此："
        "如果精灵需要用户选择路径、方案或确认风险，不能只用气泡问“选择哪个路径”，"
        "必须调用 request_user_input，让前端渲染可点击选项卡和 Other 输入。\n"
        "- request_user_input 少样本：用户说“创建一个 test.txt 文件，写入 helloworld”，"
        "但没有说明目录时，调用 request_user_input，question=\"test.txt 应该创建在哪个目录下？\"，"
        "options 可包括：label=\"Home 目录\", value=\"/home/<user>/test.txt\", "
        "description=\"不污染当前 AiMemo 仓库\"；以及 label=\"AiMemo 仓库内的明确子路径\", "
        "value=\"/home/<user>/project/AiMemo/<subdir>/test.txt\", description=\"仅当用户确实想把文件放进本项目\"。"
        "不要直接回答路径列表。\n"
        "- request_user_input 少样本：上一轮用户选择 `/home/user/demo1`、React、SQLite 写一个项目；本轮用户说“再做一个记账小程序”。"
        "这属于新的项目，不能默认复用 `/home/user/demo1`、React、SQLite 或上一轮授权，"
        "必须再次调用 request_user_input 询问目标目录，并在技术栈/数据源会影响落地时一并确认。"
        "只有用户本轮说“继续改 demo1/沿用上次目录和技术栈”，才可复用这些条件。\n"
        "- request_user_input 少样本：用户说“给应用加导出功能”，若可同时选择导出 Markdown、PDF、HTML，"
        "调用 request_user_input 且 selection_mode=\"multiple\"；若项目已有唯一导出模式可沿用，则直接执行，不要多问。\n"
        "- request_user_input 少样本：read_file 返回 truncated=true，用户又要求整文件覆盖 `/path/big.json`。"
        "调用 request_user_input，question=\"`/path/big.json` 太大，无法在单次工具调用中完整读取。你是否确认在未完整读取旧内容的情况下直接整文件覆盖？\"，"
        "options 包含 label=\"取消覆盖，改用新路径或更小范围\" 与 label=\"确认直接覆盖旧文件\"。"
        "只有用户选择确认后才设置 confirmed_overwrite_without_read=true。\n"
        "- request_user_input 少样本：exec_command_background 启动 Java 项目后，"
        "read_background_output 返回“系统找不到 mvn 命令”。这不是 final_answer 场景，"
        "而是恢复方案选择场景。调用 request_user_input，question=\"当前系统找不到 Maven，接下来你希望我采用哪种方式继续启动项目？\"，"
        "options 可包括 label=\"安装或配置 Maven\", description=\"适合长期使用 Maven 命令\"；"
        "label=\"为项目添加 Maven Wrapper\", description=\"不依赖全局 Maven，更适合项目自包含\"；"
        "label=\"改用已有 jar 或其他启动方式\", description=\"如果项目已经打包或有替代启动脚本\"。"
        "不要在最终回答里列三条方案让用户回复编号。\n"
        "- 知识库边界：当前对话只能使用用户显式挂载到该对话的知识空间。"
        "不要声称搜索了未挂载的知识库，不要要求 knowledge_search 指定 space_id，"
        "也不要绕过挂载边界做全局知库检索。"
        "只要当前对话已挂载知识空间，dispatch_context_workers 默认会先检索挂载资料；"
        "只有非常明确的闲聊或客观常识问题才会跳过首轮检索。"
        "初始上下文中的 `L3.5 挂载知识空间检索` 是首轮检索结果；"
        "如果这些片段不足以回答，才调用 knowledge_search 做补充检索。"
        "同一问题补充检索时优先使用相同 query 加 retrieval_profile=\"expanded\"，"
        "让工具从本轮 recall_cache 扩充；只有问题角度变化、缓存不足或用户要求深查时，才改写 query 或使用 deep。"
        "[K1]/[K2] 这类编号只用于内部定位检索片段，最终回答不要输出裸露编号或单独引用列表；"
        "需要说明来源时，用文档标题或自然语言融入句子。"
        "如果没有挂载或工具返回 NEED_KNOWLEDGE_MOUNT，应明确说明需要先挂载知识空间。\n"
        "- 联网搜索边界：`Lx.web 联网搜索上下文` 是本轮公网证据层，只有 planner 判断需要时才会自动搜索。"
        "当问题需要最新公共信息、官网资料、价格、计费、版本、政策、法规、新闻或明确来源 URL 时，"
        "可以使用 Lx.web，或在来源不足时调用 web_search/web_fetch 补充。"
        "当本地笔记、挂载知识库、附件或本地文件足以回答时，优先使用本地上下文，不主动联网。"
        "不要把用户私人笔记原文、未公开代码、本地路径、账号、密钥或聊天隐私放入 web_search query。"
        "如果必须外发可能含隐私的 query，先调用 request_user_input 确认。"
        "使用联网信息回答时必须列出来源 URL；涉及价格、政策、API 参数时优先引用 fetched=true 的官方来源。"
        "没有成功的 Lx.web/web_search/web_fetch 结果时，不得声称已经联网搜索。\n"
        "- 多个互不依赖的读取、搜索或信息查询可以在同一轮 tool_calls 中并行发出；"
        "有依赖关系的步骤必须等上一步 ToolMessage 返回后再继续。\n"
        "- 不要把删除、清理、覆盖配置、重建项目作为开局动作；只有定位到具体原因后才做针对性修改。\n"
        "- 最终回答只能基于已知上下文和真实工具结果，不要写”模拟展示”。\n\n"
        f"{RUNTIME_AGENT_RULES}\n\n"
        "表达规则：使用中文，简洁说明你实际完成了什么；如果工具失败，直接说明真实失败原因和下一步。"
    )


def _extract_ai_tool_calls(message) -> list[dict]:
    """提取 AIMessage.tool_calls，规整为普通 dict 列表。"""

    raw_calls = getattr(message, "tool_calls", None) or []
    result: list[dict] = []
    for call in raw_calls:
        if isinstance(call, dict):
            result.append(dict(call))
        else:
            result.append(
                {
                    "id": getattr(call, "id", ""),
                    "name": getattr(call, "name", ""),
                    "args": getattr(call, "args", {}) or {},
                }
            )
    return result


def _extract_ai_message_content(message) -> str:
    """提取 AIMessage 正文，兼容字符串和分段 content。"""

    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict) and isinstance(item.get("text"), str):
                parts.append(str(item.get("text")))
        return "".join(parts)
    return str(content or "")


def _ai_message_to_turn_message(message, *, fallback_content: str) -> TurnMessagePayload:
    """把 AIMessage 压成可进入 checkpoint 的内部消息。"""

    tool_calls = _extract_ai_tool_calls(message)
    if tool_calls:
        turn_message = _turn_message("assistant", fallback_content, name="agent")
        turn_message["tool_calls"] = tool_calls
        return turn_message
    return _turn_message("assistant", fallback_content, name="agent")


def _coerce_elf_choice_final_answer_to_tool_call(
    state: MemoryChatGraphState,
    assistant_text: str,
) -> dict | None:
    """精灵模式下把明显的普通文本选择题改成 request_user_input。

    这是精灵入口的防护网：模型偶尔会无视工具约束，直接用气泡问“请选择 1/2”。
    那样前端无法渲染选项卡。这里只在外置精灵模式、且能稳定提取出至少两个选项时介入。
    """

    if state.get("answer_mode") != "elf_bubble":
        return None
    text = assistant_text.strip()
    if not _looks_like_unstructured_choice_prompt(text):
        return None

    options = _extract_unstructured_choice_options(text)
    if len(options) < 2:
        return None

    question = _extract_unstructured_choice_question(text)
    if len(question) < 6:
        question = "你希望我按哪个选项继续？"
    tool_call_id = f"elf-choice-{int(state.get('conversation_id') or 0)}-{len(state.get('turn_messages', [])) + 1}"
    return {
        "id": tool_call_id,
        "name": REQUEST_USER_INPUT_TOOL_NAME,
        "args": {
            "question": question,
            "options": options[:4],
            "selection_mode": "single",
            "allow_other": True,
        },
    }


def _looks_like_unstructured_choice_prompt(text: str) -> bool:
    if not text:
        return False
    normalized = re.sub(r"\s+", " ", text)
    choice_keywords = [
        "请选择",
        "选择一个",
        "选一个",
        "选哪",
        "哪个选项",
        "哪种方式",
        "你希望",
        "你想要",
        "要不要",
        "是否",
        "确认",
        "方案",
        "选项",
    ]
    return any(keyword in normalized for keyword in choice_keywords)


def _extract_unstructured_choice_options(text: str) -> list[dict]:
    options: list[dict] = []
    bullet_lines: list[str] = []
    for raw_line in text.splitlines():
        line = _strip_markdown_choice_line(raw_line)
        if not line:
            continue
        numbered = re.match(
            r"^(?:选项\s*)?(?:[A-Da-d]|[1-4]|[一二三四])[\.\)、:：]\s*(?P<body>.+)$",
            line,
        )
        if numbered:
            options.append(_choice_option_from_text(numbered.group("body"), len(options)))
            continue
        bullet = re.match(r"^(?:[-*•]\s+)(?P<body>.+)$", raw_line.strip())
        if bullet:
            bullet_lines.append(bullet.group("body").strip())

    if len(options) < 2 and len(bullet_lines) >= 2:
        options = [_choice_option_from_text(line, index) for index, line in enumerate(bullet_lines[:4])]

    deduped: list[dict] = []
    seen_values: set[str] = set()
    for option in options:
        value = str(option.get("value") or "").strip()
        if not value or value in seen_values:
            continue
        seen_values.add(value)
        deduped.append(option)
    return deduped[:4]


def _choice_option_from_text(text: str, index: int) -> dict:
    cleaned = re.sub(r"\s+", " ", text).strip(" -_*`：:")
    label = cleaned
    description = ""
    split_match = re.match(r"^(?P<label>[^：:]{1,24})[：:]\s*(?P<description>.+)$", cleaned)
    if split_match:
        label = split_match.group("label").strip()
        description = split_match.group("description").strip()
    return {
        "id": f"option-{index + 1}",
        "label": label[:36] or f"选项 {index + 1}",
        "value": cleaned,
        "description": description[:96],
    }


def _extract_unstructured_choice_question(text: str) -> str:
    question_lines: list[str] = []
    for raw_line in text.splitlines():
        line = _strip_markdown_choice_line(raw_line)
        if not line:
            continue
        if re.match(r"^(?:选项\s*)?(?:[A-Da-d]|[1-4]|[一二三四])[\.\)、:：]\s*.+$", line):
            break
        if re.match(r"^(?:[-*•]\s+).+$", raw_line.strip()):
            break
        question_lines.append(line)
    question = " ".join(question_lines).strip()
    question = re.sub(r"(?:可以|请)?(?:从)?(?:下面|以下)(?:几个)?(?:选项|方案)(?:里)?(?:选一个)?[：:]?$", "", question).strip()
    return question[:160]


def _strip_markdown_choice_line(line: str) -> str:
    return line.strip().strip("`").strip()


def _default_local_operator_workspace_roots() -> list[str]:
    """返回默认本地读取 workspace roots。

    启动脚本会 `cd backend` 后再启动 uvicorn；如果直接使用 Path.cwd()，
    Local Operator 就读不到 docs/frontend/desktop 等仓库根目录内容。
    这里根据当前文件位置反推出仓库根目录，并默认加入当前用户 Home。

    读取本身没有 write/exec 的副作用，所以 read-only 阶段默认开放本机固定盘符。
    真正的安全边界放在 LocalOperatorPolicy/LocalFilesystemService：
    敏感文件、数据库、设备路径、UNC 网络路径和大小限制仍会被拦截。
    """

    roots = [Path(__file__).resolve().parents[5], Path.home(), *_local_fixed_drive_roots()]
    roots.extend(Path(root).expanduser() for root in _configured_local_operator_workspace_roots())
    normalized: list[str] = []
    seen: set[str] = set()
    for root in roots:
        resolved = str(root.resolve())
        if resolved not in seen:
            normalized.append(resolved)
            seen.add(resolved)
    return normalized


def _local_fixed_drive_roots() -> list[Path]:
    r"""返回本机固定盘符根目录，用于 read-only Local Operator。

    Windows 上用户经常直接给 `C:\...`、`D:\...` 这样的绝对路径。如果默认只授权
    Home，模型就会在回答层误以为自己“看不到 C 盘”。参考 通用 coding agent 的设计：
    读取能力应由工具真实执行并返回错误，而不是由模型预先拒绝。
    """

    import os

    if os.name != "nt":
        return [Path("/")]
    roots: list[Path] = []
    for code in range(ord("A"), ord("Z") + 1):
        root = Path(f"{chr(code)}:/")
        if root.exists():
            roots.append(root)
    return roots


def _configured_local_operator_workspace_roots() -> list[str]:
    """解析用户在 .env 中追加的 Local Operator read 根目录。

    支持分号或逗号分隔，例如：
      LOCAL_OPERATOR_WORKSPACE_ROOTS=E:\\Ai记;D:\\资料;~/Documents
    """

    raw_value = settings.local_operator_workspace_roots.strip()
    if not raw_value:
        return []
    return [part.strip() for part in re.split(r"[;,]", raw_value) if part.strip()]






def route_answer_mode(state: MemoryChatGraphState) -> str:
    """根据 answer_mode 选择回答生成分支。

    ReAct 版普通 text 回答已经由 agent 节点写入 assistant_answer；
    桌面精灵外置聊天仍需要气泡节点重写为 bubble JSON。
    两条分支最后都必须写入 assistant_answer，确保 persist_messages 可以复用。
    """

    if state.get("answer_mode") == "elf_bubble":
        return "generate_elf_bubble_answer"
    return "persist_messages"


def build_generate_elf_bubble_answer_node(
    bubble_answer_generator: ElfBubbleAnswerGenerator | None = None,
):
    """生成桌面精灵气泡回复。

    该节点是 generate_answer 的并行替代分支：它面向外置精灵，要求模型把回答拆成
    多个语义完整的气泡，并为每个气泡给出 emoji。为了让下游持久化保持简单，
    节点仍会把所有气泡 text 合并为 assistant_answer。
    """

    def generate_elf_bubble_answer(state: MemoryChatGraphState) -> MemoryChatGraphState:
        user_message = _resolve_user_message(state)
        recent_messages = state.get("recent_messages", [])
        retrieved_chunks = state.get("retrieved_chunks", [])
        needs_retrieval = bool(state.get("needs_retrieval", False))
        retrieval_grade = state.get("retrieval_grade", "none")
        if bubble_answer_generator is None:
            parts = generate_memory_chat_elf_bubble_answer(
                user_message,
                recent_messages,
                retrieved_chunks,
                needs_retrieval,
                retrieval_grade,
                prompt_context=state.get("prompt_context", ""),
                turn_messages=state.get("turn_messages", []),
            )
        else:
            raw_parts = bubble_answer_generator(
                user_message,
                recent_messages,
                retrieved_chunks,
                needs_retrieval,
                retrieval_grade,
            )
            # 测试桩或后续替代生成器可能直接返回旧版 emoji；这里统一归一化，
            # 保证 graph state、持久化消息和桌面端展示使用同一套表情枚举。
            parts = [
                {
                    "text": part["text"],
                    "emoji": _normalize_elf_emoji(str(part.get("emoji") or "idle_soft")),
                }
                for part in raw_parts
                if part.get("text")
            ]
        parts = _drop_trailing_elf_listening_fillers(parts)
        return {
            "elf_bubble_answer_parts": parts,
            "assistant_answer": "\n\n".join(part["text"] for part in parts if part.get("text")),
        }

    return generate_elf_bubble_answer



