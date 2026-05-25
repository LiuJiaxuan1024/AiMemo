"""验证 AGENTS.md 的项目规则被注入到 AiMemo 运行时 agent 的 system prompt。"""

from app.agent.project_rules import RUNTIME_AGENT_RULES
from app.agent.graphs.local_operator.nodes import _build_local_operator_planner_prompt
from app.agent.graphs.memory_chat.nodes import _build_react_agent_system_prompt


def test_runtime_rules_constant_is_nonempty_and_mentions_aimemo():
    assert RUNTIME_AGENT_RULES.strip()
    assert "AiMemo" in RUNTIME_AGENT_RULES
    assert "没有明确指出工作目录" in RUNTIME_AGENT_RULES
    assert "必须保持项目上下文隔离" in RUNTIME_AGENT_RULES
    assert "目录、技术栈、依赖、配置、数据源、账号、风险授权或用户偏好" in RUNTIME_AGENT_RULES
    assert "不能复用旧项目条件" in RUNTIME_AGENT_RULES
    assert "项目上下文混淆反例" in RUNTIME_AGENT_RULES
    assert "结构化选择框" in RUNTIME_AGENT_RULES
    assert "普通 assistant 文本输出编号列表" in RUNTIME_AGENT_RULES


def test_react_system_prompt_includes_runtime_rules():
    prompt = _build_react_agent_system_prompt()
    assert "你是 AiMemo 的主 ReAct agent" in prompt
    assert "AiMemo 项目规则" in prompt
    assert "必须调用 request_user_input" in prompt
    assert "创建一个 test.txt 文件" in prompt
    assert "selection_mode=\"multiple\"" in prompt
    assert "confirmed_overwrite_without_read" in prompt
    assert "无法在单次工具调用中完整读取" in prompt
    assert "先按默认规则调用基础工具推进任务" in prompt
    assert "不要一上来就询问是否绕过规则" in prompt
    assert "再做一个记账小程序" in prompt
    assert "不能默认复用 `/home/user/demo1`、React、SQLite 或上一轮授权" in prompt
    assert RUNTIME_AGENT_RULES in prompt


def test_planner_prompt_renamed_to_aimemo_and_asks_about_working_dir():
    prompt = _build_local_operator_planner_prompt("帮我做一个 RAG 项目")
    assert "你是 AiMemo 的 Local Operator" in prompt
    assert "Ai 记" not in prompt
    assert "没有明确指定目标目录" in prompt
    assert "confirmed_overwrite_without_read" in prompt
    assert RUNTIME_AGENT_RULES in prompt
