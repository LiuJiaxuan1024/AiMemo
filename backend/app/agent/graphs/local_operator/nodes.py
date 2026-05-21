from collections.abc import Callable
from contextlib import AbstractContextManager
from dataclasses import dataclass
import json
from pathlib import Path
import re
from typing import Any

from langchain_core.messages import HumanMessage
from sqlmodel import Session

from app.ai.json_utils import parse_json_object
from app.agent.model import get_planner_chat_model
from app.agent.graphs.local_operator.state import (
    LocalOperatorAction,
    LocalOperatorObservation,
    LocalOperatorState,
)
from app.local_operator.policy import LocalOperatorPolicy
from app.local_operator.tools import create_read_tools


SessionFactory = Callable[[], AbstractContextManager[Session]]
ReadPlanner = Callable[[str], LocalOperatorAction | None]

READ_TOOL_NAMES = {
    "list_dir",
    "read_file",
    "search_files",
    "search_text",
    "get_file_info",
}
WRITE_TOOL_NAMES = {
    "write_file",
}
ALLOWED_LOCAL_OPERATOR_TOOLS = READ_TOOL_NAMES | WRITE_TOOL_NAMES


@dataclass(frozen=True)
class PlannerDecision:
    """Local Operator planner 的结构化决策。"""

    needs_tool: bool
    tool_name: str
    arguments: dict[str, Any]
    confidence: float
    reason: str


def build_plan_tool_use_node(
    planner: ReadPlanner | None = None,
) -> Callable[[LocalOperatorState], LocalOperatorState]:
    """规划本轮是否需要调用本地工具。

    规划分三层：
      1. 明确规则快路径：例如“读取文件”“列出目录”“创建文件”，直接选工具。
      2. 明显无关的普通聊天：直接跳过，避免每轮都多一次 LLM 延迟。
      3. 模糊但像本地操作的问题：调用轻量 LLM planner 判断是否需要工具、工具名和参数。
    """

    def plan_tool_use(state: LocalOperatorState) -> LocalOperatorState:
        user_input = state.get("user_input", "").strip()
        action = _rule_plan_action(user_input, workspace_roots=state.get("workspace_roots") or ["."])
        if action is None and _looks_like_local_operator_candidate(user_input):
            action = (planner or _llm_plan_tool_action)(user_input)
        if action is None:
            return {
                "needs_tool": False,
                "tool_intent": "",
                # 兼容旧字段：历史 checkpoint/测试仍可能读取 need_local_read。
                "need_local_read": False,
                "read_intent": "",
                "tool_budget": 0,
                "planned_actions": [],
                "next_action": None,
                "tool_calls": [],
                "observations": [],
                "enough_evidence": True,
                "final_answer": "",
                "error": "",
            }

        planned_actions = _expand_planned_actions(action)
        return {
            "needs_tool": True,
            "tool_intent": action.get("reason", ""),
            # 兼容旧字段：含 write_file 时这个字段表示“需要 Local Operator 工具”。
            "need_local_read": True,
            "read_intent": action.get("reason", ""),
            "tool_budget": int(state.get("tool_budget") or 4),
            "planned_actions": planned_actions,
            "next_action": None,
            "tool_calls": [],
            "observations": [],
            "enough_evidence": False,
            "final_answer": "",
            "error": "",
        }

    return plan_tool_use


def build_select_tool_node() -> Callable[[LocalOperatorState], LocalOperatorState]:
    """从计划队列中选择下一次工具调用。

    这个节点只负责“取队首 action”，真正的 read/write 分流由 graph 条件边完成。
    这样 Mermaid 图会明确展示工具路由，而不是把所有工具塞进一个 run 节点。
    """

    def select_tool(state: LocalOperatorState) -> LocalOperatorState:
        planned_actions = list(state.get("planned_actions", []))
        if not planned_actions:
            return {"next_action": None, "enough_evidence": True}
        return {"next_action": planned_actions.pop(0), "planned_actions": planned_actions}

    return select_tool


def build_run_read_tool_node(session_factory: SessionFactory) -> Callable[[LocalOperatorState], LocalOperatorState]:
    """执行受控 read 工具分支。

    写工具不会从这里执行。读写分流放在 `route_after_select_tool`，目的是让子图可视化
    能直接看出本轮走了 read 分支还是 write 分支。
    """

    def run_read_tool(state: LocalOperatorState) -> LocalOperatorState:
        return _run_tool_action(state, session_factory=session_factory, allowed_tool_names=READ_TOOL_NAMES)

    return run_read_tool


def build_run_write_tool_node(session_factory: SessionFactory) -> Callable[[LocalOperatorState], LocalOperatorState]:
    """执行受控 write 工具分支。

    当前只有 `write_file`。工具内部仍会做 workspace 授权、敏感文件拦截、
    read-before-write 保护和 agent_operations 审计。
    """

    def run_write_tool(state: LocalOperatorState) -> LocalOperatorState:
        return _run_tool_action(state, session_factory=session_factory, allowed_tool_names=WRITE_TOOL_NAMES)

    return run_write_tool


def build_observe_tool_result_node() -> Callable[[LocalOperatorState], LocalOperatorState]:
    """判断当前工具结果是否足够。

    当前循环先消费 planner 生成的工具队列；如果队列为空或预算耗尽就结束。
    后续如果加入 ReAct 式二次规划，可以在这里根据 observation 追加 planned_actions，
    形成“选择工具 -> 执行工具 -> 观察 -> 再决定”的完整工具循环。
    """

    def observe_tool_result(state: LocalOperatorState) -> LocalOperatorState:
        if int(state.get("tool_budget") or 0) <= 0:
            return {"enough_evidence": True}
        if not state.get("planned_actions"):
            return {"enough_evidence": True}
        return {"enough_evidence": False}

    return observe_tool_result


def build_finish_without_tool_node() -> Callable[[LocalOperatorState], LocalOperatorState]:
    """本轮不需要本地工具，快速结束。"""

    def finish_without_tool(state: LocalOperatorState) -> LocalOperatorState:
        return {"final_answer": "", "enough_evidence": True}

    return finish_without_tool


def build_summarize_findings_node() -> Callable[[LocalOperatorState], LocalOperatorState]:
    """把工具观测结果整理成可放入 Memory Chat prompt 的上下文。"""

    def summarize_findings(state: LocalOperatorState) -> LocalOperatorState:
        observations = state.get("observations", [])
        if not observations:
            return {"final_answer": ""}

        lines = ["## 本地工具调用结果"]
        for observation in observations:
            lines.extend(_observation_to_lines(observation))
        return {"final_answer": "\n".join(lines)}

    return summarize_findings


def route_after_plan(state: LocalOperatorState) -> str:
    """plan_tool_use 后的条件边。"""

    return "select_tool" if state.get("needs_tool", state.get("need_local_read", False)) else "finish_without_tool"


def route_after_select_tool(state: LocalOperatorState) -> str:
    """select_tool 后按工具类型分流到 read/write 执行节点。"""

    action = state.get("next_action") or {}
    tool_name = str(action.get("tool_name") or "")
    if tool_name in WRITE_TOOL_NAMES:
        return "run_write_tool"
    return "run_read_tool"


def route_after_observe(state: LocalOperatorState) -> str:
    """observe_tool_result 后的条件边。"""

    return "summarize_findings" if state.get("enough_evidence", True) else "select_tool"


def _run_tool_action(
    state: LocalOperatorState,
    *,
    session_factory: SessionFactory,
    allowed_tool_names: set[str],
) -> LocalOperatorState:
    """执行当前 next_action，并把标准化 observation 写回 state。

    参数：
      state: Local Operator 当前图状态，必须包含 next_action。
      session_factory: 数据库 session 工厂，传给工具内部审计模块。
      allowed_tool_names: 当前执行分支允许的工具名集合，用于防止路由错误或 planner 越权。
    """

    action = state.get("next_action")
    if not action:
        return {"enough_evidence": True}

    policy = LocalOperatorPolicy.from_roots(state.get("workspace_roots") or ["."])
    tools = create_read_tools(
        session_factory=session_factory,
        policy=policy,
        conversation_id=state.get("conversation_id"),
        turn_id=state.get("turn_id"),
        known_existing_paths=_known_existing_paths_from_observations(state.get("observations", [])),
    )
    tool_name = str(action.get("tool_name") or "")
    arguments = dict(action.get("arguments") or {})
    if tool_name not in allowed_tool_names:
        observation: LocalOperatorObservation = {
            "tool_name": tool_name,
            "arguments": arguments,
            "ok": False,
            "data": {},
            "error_code": "INVALID_ARGUMENT",
            "message": f"工具 `{tool_name}` 不属于当前执行分支。",
            "blocked": True,
        }
    elif tool_name not in tools:
        observation = {
            "tool_name": tool_name,
            "arguments": arguments,
            "ok": False,
            "data": {},
            "error_code": "INVALID_ARGUMENT",
            "message": f"未知本地工具：{tool_name}",
            "blocked": True,
        }
    else:
        raw_result = tools[tool_name].invoke(arguments)
        payload = json.loads(str(raw_result))
        observation = {
            "tool_name": tool_name,
            "arguments": arguments,
            "ok": bool(payload.get("ok")),
            "data": dict(payload.get("data") or {}),
            "error_code": str(payload.get("error_code") or ""),
            "message": str(payload.get("message") or ""),
            "blocked": bool(payload.get("blocked", False)),
        }

    return {
        "tool_calls": [*state.get("tool_calls", []), action],
        "observations": [*state.get("observations", []), observation],
        "tool_budget": max(int(state.get("tool_budget") or 0) - 1, 0),
        "next_action": None,
    }


def _rule_plan_action(user_input: str, *, workspace_roots: list[str]) -> LocalOperatorAction | None:
    normalized = user_input.strip()
    if _looks_like_rule_write_request(normalized):
        path = _extract_path(normalized)
        content = _extract_write_content(normalized)
        if not path or not content:
            return None
        return {
            "tool_name": "write_file",
            "arguments": {
                "path": path,
                "content": content,
                "overwrite": _looks_like_overwrite_request(normalized),
            },
            "reason": "用户明确要求写入本地文件。",
        }
    if not _looks_like_rule_read_request(normalized):
        return None

    path = _extract_path(normalized)
    if _looks_like_project_existence_request(normalized):
        if not path and _looks_like_whole_computer_request(normalized):
            return {
                "tool_name": "search_files",
                "arguments": {
                    "root": _default_whole_computer_search_root(workspace_roots),
                    "pattern": _extract_project_name(normalized) or "AiMemo",
                    "max_results": 50,
                    "include_hidden": False,
                },
                "reason": "用户询问当前电脑/Home 下是否存在某项目，优先在用户根目录内搜索项目名。",
            }
        return {
            "tool_name": "get_file_info",
            "arguments": {"path": path or "."},
            "reason": "用户询问本地是否存在当前项目或路径。",
        }
    if any(keyword in normalized for keyword in ["列出", "目录", "文件夹", "list"]):
        return {
            "tool_name": "list_dir",
            "arguments": {"path": path or ".", "max_entries": 100, "include_hidden": False},
            "reason": "用户要求列出本地目录。",
        }
    if any(keyword in normalized for keyword in ["搜索内容", "查找内容", "grep", "包含"]):
        query = _extract_quoted_text(normalized) or _extract_search_query(normalized)
        return {
            "tool_name": "search_text",
            "arguments": {"root": path or ".", "query": query, "max_results": 50, "context_lines": 2},
            "reason": "用户要求搜索本地文本内容。",
        }
    if any(keyword in normalized for keyword in ["搜索文件", "找文件", "文件名"]):
        pattern = _extract_quoted_text(normalized) or _extract_search_query(normalized)
        return {
            "tool_name": "search_files",
            "arguments": {"root": path or ".", "pattern": pattern, "max_results": 50, "include_hidden": False},
            "reason": "用户要求按文件名搜索。",
        }
    if any(keyword in normalized for keyword in ["信息", "大小", "修改时间", "元信息"]):
        return {
            "tool_name": "get_file_info",
            "arguments": {"path": path or "."},
            "reason": "用户要求查看文件或目录信息。",
        }
    return {
        "tool_name": "read_file",
        "arguments": {"path": path or ".", "start_line": None, "end_line": None, "max_bytes": 65536},
        "reason": "用户要求读取本地文件。",
    }


def _looks_like_rule_read_request(text: str) -> bool:
    action_keywords = ["读取", "读一下", "打开", "看看文件", "查看文件", "列出", "搜索", "查找", "grep", "list"]
    object_keywords = ["文件", "目录", "文件夹", "路径", ".py", ".ts", ".tsx", ".md", ".txt", ".json"]
    return (
        any(keyword in text for keyword in action_keywords)
        and any(keyword in text for keyword in object_keywords)
    ) or _looks_like_project_existence_request(text)


def _looks_like_rule_write_request(text: str) -> bool:
    """识别明确写文件请求。

    写入有副作用，所以规则快路径要求同时出现写入动作、文件对象和可提取内容。
    模糊请求交给 LLM planner；如果仍无法确定路径或内容，就不执行工具。
    """

    action_keywords = ["写入", "创建文件", "创建", "新建文件", "新建", "保存到", "覆盖写入", "write"]
    object_keywords = ["文件", ".py", ".ts", ".tsx", ".md", ".txt", ".json", ".css", ".html", ".yaml", ".yml"]
    return any(keyword in text for keyword in action_keywords) and any(keyword in text for keyword in object_keywords)


def _looks_like_local_operator_candidate(text: str) -> bool:
    """判断是否值得调用 LLM planner。

    Memory Chat 每轮都会并行跑 Local Operator worker。如果这里过宽，普通聊天会多一次
    planner LLM；如果过窄，自然语言本地操作又识别不到。所以候选规则只看“本地/文件/项目”
    语义，不直接决定工具。
    """

    candidate_keywords = [
        "当前电脑",
        "本机",
        "本地",
        "电脑上",
        "工作区",
        "workspace",
        "项目",
        "仓库",
        "repo",
        "repository",
        "文件",
        "目录",
        "文件夹",
        "路径",
        "代码",
        "源码",
        "Ai记",
        "Ai 记",
        "AiMemo",
    ]
    return any(keyword in text for keyword in candidate_keywords)


def _llm_plan_tool_action(user_input: str) -> LocalOperatorAction | None:
    """使用轻量 LLM 判断是否需要本地文件工具。

    这是规则快路径的兜底，而不是安全边界。模型只能返回白名单工具计划；
    真正执行时仍会经过工具白名单、workspace 权限和敏感文件拦截。
    """

    prompt = _build_local_operator_planner_prompt(user_input)
    try:
        response = get_planner_chat_model().invoke([HumanMessage(content=prompt)])
        payload = parse_json_object(str(response.content))
        decision = _parse_planner_decision(payload)
    except Exception:
        return None
    if not decision.needs_tool or decision.confidence < 0.55:
        return None
    if decision.tool_name not in ALLOWED_LOCAL_OPERATOR_TOOLS:
        return None
    return {
        "tool_name": decision.tool_name,
        "arguments": _normalize_tool_arguments(decision.tool_name, decision.arguments),
        "reason": f"LLM planner 判断需要本地工具：{decision.reason}",
    }


def _expand_planned_actions(action: LocalOperatorAction) -> list[LocalOperatorAction]:
    """把单个高层动作展开为 graph 可顺序执行的工具队列。

    覆盖已有文件时，`write_file` 必须先观察目标路径。这样既保留 Claude Code
    read-before-write 的保护思想，也避免模型直接用整文件覆盖误伤用户刚改的内容。
    """

    if action.get("tool_name") != "write_file":
        return [action]
    arguments = dict(action.get("arguments") or {})
    if not arguments.get("overwrite"):
        return [action]
    path = str(arguments.get("path") or ".")
    return [
        {
            "tool_name": "get_file_info",
            "arguments": {"path": path},
            "reason": "覆盖写入前先查看目标文件，满足 read-before-write 保护。",
        },
        action,
    ]


def _build_local_operator_planner_prompt(user_input: str) -> str:
    return (
        "你是 Ai 记的 Local Operator 本地文件工具规划器。判断用户这句话是否需要读取或写入授权 workspace 内的本地文件。\n"
        "只能规划白名单文件工具，不能执行命令，不能访问网络。\n"
        "规划 write_file 必须非常保守：只有用户明确要求创建/写入/覆盖本地文件，并给出目标路径和内容时才允许。\n\n"
        "能力说明：\n"
        "- 你可以规划读取本机文件系统中的文件或目录，包括用户给出的绝对路径。\n"
        "- 你可以规划写入授权 workspace 内的普通文本文件；写入已有文件时必须设置 overwrite=true，工具会要求先读取或查看该文件。\n"
        "- write_file 的 content 必须是用户明确给出的正文，或用户明确要求你生成且你已经能在本节点中完整生成的正文。\n"
        "- 不要把“用于写...”“准备写...”“创建一个...文件”理解成可以写入模板；禁止写入“此处填写”“待补充”“TODO”等占位内容。\n"
        "- 如果用户提供了路径，先假设路径值得尝试；路径不存在、不可读、敏感或越权时，工具会返回真实错误。\n"
        "- 不要因为路径在 C 盘、D 盘、系统目录或 Home 之外，就在规划阶段拒绝；是否允许由工具策略决定。\n"
        "- 你不能凭空声称“我无法访问你的电脑/系统日志/硬盘”。需要确认时必须调用本地文件工具。\n"
        "- 当前不能执行命令，不能访问网络。\n\n"
        "可用工具：\n"
        "- list_dir(path, max_entries, include_hidden): 列目录。\n"
        "- read_file(path, start_line, end_line, max_bytes): 读取文本文件。\n"
        "- search_files(root, pattern, max_results, include_hidden): 按文件名搜索。\n"
        "- search_text(root, query, include_glob, max_results, context_lines): 搜索文本内容。\n"
        "- get_file_info(path): 查看文件或目录是否存在、大小、修改时间。\n\n"
        "- write_file(path, content, overwrite): 创建或整文件写入文本文件。优先用于新建文件或完整重写；局部修改暂不规划。\n\n"
        "判断原则：\n"
        "- 用户询问本机/电脑/工作区里是否存在某项目、仓库、目录或文件，需要本地读取。\n"
        "- 用户要求查看、确认、搜索、打开、列出本地文件/目录，需要本地读取。\n"
        "- 普通聊天、记忆问答、常识问题、数学题不需要本地读取。\n"
        "- 用户只是讨论代码方案、询问如何修改时，不要写文件；只有明确要求“帮我写入/创建/保存到某文件”才使用 write_file。\n"
        "- 如果用户只说“创建一个用于写 X 的文件”，但没有要求你现在生成 X 的正文，也没有提供正文，不要调用 write_file；应让最终回答节点说明需要先生成正文。\n"
        "- 如果用户没有给具体路径，但问的是当前项目/这个仓库/当前工作区，用 path/root = \".\"。\n"
        "- 如果用户问的是当前电脑/本机/Home/用户目录里有没有某个项目或文件，优先用 search_files，root = \"~\"。\n"
        "- 如果只是确认当前项目是否存在，优先用 get_file_info，path = \".\"。\n"
        "- 如果用户给出明确文件路径，优先用 read_file；如果给出明确目录路径，优先用 list_dir 或 get_file_info。\n"
        "- 不要自己编造未出现的绝对路径；但用户明确给出的绝对路径可以原样传给工具。\n\n"
        "只返回 JSON，不要输出其他文本。格式：\n"
        "{"
        "\"needs_tool\":true,"
        "\"tool_name\":\"get_file_info\","
        "\"arguments\":{\"path\":\".\"},"
        "\"confidence\":0.8,"
        "\"reason\":\"简短原因\""
        "}\n\n"
        f"用户输入：{user_input}"
    )


def _parse_planner_decision(payload: dict[str, Any]) -> PlannerDecision:
    return PlannerDecision(
        needs_tool=bool(payload.get("needs_tool", payload.get("need_local_read", False))),
        tool_name=str(payload.get("tool_name") or ""),
        arguments=dict(payload.get("arguments") or {}),
        confidence=float(payload.get("confidence", 0.0)),
        reason=str(payload.get("reason") or ""),
    )


def _normalize_tool_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """规整 LLM 返回的工具参数，补默认值并丢弃无关字段。"""

    if tool_name == "list_dir":
        return {
            "path": str(arguments.get("path") or "."),
            "max_entries": int(arguments.get("max_entries") or 100),
            "include_hidden": bool(arguments.get("include_hidden", False)),
        }
    if tool_name == "read_file":
        return {
            "path": str(arguments.get("path") or "."),
            "start_line": arguments.get("start_line"),
            "end_line": arguments.get("end_line"),
            "max_bytes": int(arguments.get("max_bytes") or 65536),
        }
    if tool_name == "search_files":
        return {
            "root": str(arguments.get("root") or "."),
            "pattern": str(arguments.get("pattern") or "*"),
            "max_results": int(arguments.get("max_results") or 50),
            "include_hidden": bool(arguments.get("include_hidden", False)),
        }
    if tool_name == "search_text":
        return {
            "root": str(arguments.get("root") or "."),
            "query": str(arguments.get("query") or ""),
            "include_glob": arguments.get("include_glob"),
            "max_results": int(arguments.get("max_results") or 50),
            "context_lines": int(arguments.get("context_lines") or 2),
        }
    if tool_name == "get_file_info":
        return {"path": str(arguments.get("path") or ".")}
    if tool_name == "write_file":
        return {
            "path": str(arguments.get("path") or "."),
            "content": str(arguments.get("content") or ""),
            "overwrite": bool(arguments.get("overwrite", False)),
        }
    return {}


def _looks_like_project_existence_request(text: str) -> bool:
    """识别“我电脑上有没有某项目/目录”这类本地存在性问题。

    用户不一定会说“读取/搜索文件”，但语义上需要检查授权 workspace。
    第一版仍只检查当前授权 workspace，不扫描整台电脑。
    """

    location_keywords = ["当前电脑", "本机", "本地", "电脑", "硬盘", "workspace", "工作区"]
    existence_keywords = ["有没有", "是否有", "存在", "在不在", "是不是有", "有这个"]
    target_keywords = ["项目", "目录", "文件夹", "仓库", "repo", "repository", "Ai记", "Ai 记", "AiMemo"]
    return (
        any(keyword in text for keyword in location_keywords)
        and any(keyword in text for keyword in existence_keywords)
        and any(keyword in text for keyword in target_keywords)
    )


def _looks_like_whole_computer_request(text: str) -> bool:
    """识别用户是在问电脑/Home 范围，而不是当前仓库范围。"""

    whole_computer_keywords = ["当前电脑", "本机", "电脑上", "电脑", "硬盘", "用户目录", "Home", "home"]
    current_project_keywords = ["当前项目", "当前仓库", "这个仓库", "当前工作区", "workspace"]
    return any(keyword in text for keyword in whole_computer_keywords) and not any(
        keyword in text for keyword in current_project_keywords
    )


def _default_whole_computer_search_root(workspace_roots: list[str]) -> str:
    """为“当前电脑/本机”问题选择默认搜索根。

    Local Operator 的授权根通常是 `[项目根目录, Home, 用户追加目录...]`。
    这里不假设 `~` 一定被授权：测试和用户自定义 roots 里可能传入的是其他目录。

    参数：
      workspace_roots: 当前 graph 传入的授权读取根目录。

    返回：
      一个已经位于授权 roots 内的搜索根。优先使用 Home；没有 Home 时使用第二个 root；
      如果只有一个 root，则退回当前工作区 `.`。
    """

    if not workspace_roots:
        return "."
    resolved_roots = [Path(root).expanduser().resolve() for root in workspace_roots]
    home = Path.home().resolve()
    if any(root == home for root in resolved_roots):
        return "~"
    if len(resolved_roots) >= 2:
        return str(resolved_roots[1])
    return "."


def _extract_project_name(text: str) -> str:
    """从“有没有 X 项目/仓库/目录”中提取一个保守的搜索词。"""

    quoted = _extract_quoted_text(text)
    if quoted:
        return quoted.strip()
    for candidate in ["AiMemo", "Ai记", "Ai 记"]:
        if candidate in text:
            return candidate.replace(" ", "")
    match = re.search(r"有没有\s*([A-Za-z0-9_.\-\u4e00-\u9fff ]{2,30}?)(?:这个)?(?:项目|仓库|目录|文件夹)", text)
    if match:
        return match.group(1).strip().replace(" ", "")
    return ""


def _extract_path(text: str) -> str:
    quoted = _extract_quoted_text(text)
    if quoted and ("/" in quoted or "\\" in quoted or "." in quoted):
        return quoted
    match = re.search(r"([A-Za-z]:[\\/][^\s，。；;]+|(?:[\w.\-\u4e00-\u9fff]+[\\/])+[^\s，。；;]+|[\w.\-\u4e00-\u9fff]+\.[A-Za-z0-9]+)", text)
    return match.group(1) if match else ""


def _extract_quoted_text(text: str) -> str:
    match = re.search(r"[\"“']([^\"”']+)[\"”']", text)
    return match.group(1).strip() if match else ""


def _extract_search_query(text: str) -> str:
    for marker in ["搜索内容", "查找内容", "搜索文件", "找文件", "包含", "grep", "搜索", "查找"]:
        if marker in text:
            return text.split(marker, 1)[-1].strip(" ：:，。") or text
    return text


def _extract_write_content(text: str) -> str:
    """从用户输入中提取要写入文件的正文。"""

    quoted = _extract_quoted_text(text)
    if quoted and not ("/" in quoted or "\\" in quoted):
        return quoted
    for marker in ["内容是", "内容为", "写入", "保存"]:
        if marker in text:
            return text.split(marker, 1)[-1].strip(" ：:，。")
    return ""


def _looks_like_overwrite_request(text: str) -> bool:
    return any(keyword in text for keyword in ["覆盖", "重写", "overwrite", "替换整个文件"])


def _observation_to_lines(observation: LocalOperatorObservation) -> list[str]:
    tool_name = observation.get("tool_name", "")
    if not observation.get("ok"):
        return [
            f"- 工具 `{tool_name}` 执行失败：{observation.get('error_code', '')} {observation.get('message', '')}".strip()
        ]

    data: dict[str, Any] = dict(observation.get("data") or {})
    if tool_name == "read_file":
        return [
            f"- 已读取 `{data.get('relative_path')}` 第 {data.get('line_start')}-{data.get('line_end')} 行：",
            "```text",
            str(data.get("numbered_content") or data.get("content") or ""),
            "```",
        ]
    if tool_name == "list_dir":
        entries = data.get("entries") or []
        return [f"- `{data.get('relative_path')}` 目录内容：", *_format_entries(entries)]
    if tool_name == "search_files":
        matches = data.get("matches") or []
        return [f"- 文件名搜索 `{data.get('pattern')}`：", *_format_matches(matches)]
    if tool_name == "search_text":
        matches = data.get("matches") or []
        return [f"- 文本搜索 `{data.get('query')}`：", *_format_text_matches(matches)]
    if tool_name == "get_file_info":
        return [
            f"- `{data.get('relative_path')}`: path={data.get('path')}, kind={data.get('kind')}, size={data.get('size')}, modified_at={data.get('modified_at')}"
        ]
    if tool_name == "write_file":
        return [
            f"- 已{ '更新' if data.get('type') == 'update' else '创建' } `{data.get('relative_path')}`，写入 {data.get('bytes_written')} bytes，hash={data.get('content_hash')}。"
        ]
    return [f"- `{tool_name}` 返回：{data}"]


def _known_existing_paths_from_observations(observations: list[LocalOperatorObservation]) -> set[str]:
    """收集本轮已经读取或查看过的路径，供 write_file 覆盖保护使用。"""

    known_paths: set[str] = set()
    for observation in observations:
        if not observation.get("ok"):
            continue
        if observation.get("tool_name") not in {"read_file", "get_file_info"}:
            continue
        data = dict(observation.get("data") or {})
        for key in ["path", "relative_path"]:
            value = data.get(key)
            if value:
                known_paths.add(str(value))
    return known_paths


def _format_entries(entries: list[dict[str, Any]]) -> list[str]:
    if not entries:
        return ["  - 没有可展示条目。"]
    return [f"  - {entry.get('kind')}: {entry.get('relative_path')}" for entry in entries[:50]]


def _format_matches(matches: list[dict[str, Any]]) -> list[str]:
    if not matches:
        return ["  - 没有匹配文件。"]
    return [f"  - {match.get('relative_path')}" for match in matches[:50]]


def _format_text_matches(matches: list[dict[str, Any]]) -> list[str]:
    if not matches:
        return ["  - 没有匹配内容。"]
    lines = []
    for match in matches[:20]:
        lines.append(f"  - {match.get('relative_path')}:{match.get('line')}")
        lines.append(f"    {str(match.get('preview') or '').replace(chr(10), ' / ')}")
    return lines
