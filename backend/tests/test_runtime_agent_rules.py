"""验证 AGENTS.md 的项目规则被注入到 AiMemo 运行时 agent 的 system prompt。"""

from app.agent.project_rules import RUNTIME_AGENT_RULES
from app.agent.graphs.local_operator.nodes import _build_local_operator_planner_prompt
from app.agent.graphs.memory_chat.nodes import _build_react_agent_system_prompt


def test_runtime_rules_constant_is_nonempty_and_mentions_aimemo():
    assert RUNTIME_AGENT_RULES.strip()
    assert "AiMemo" in RUNTIME_AGENT_RULES
    assert "没有明确指出工作目录" in RUNTIME_AGENT_RULES


def test_react_system_prompt_includes_runtime_rules():
    prompt = _build_react_agent_system_prompt()
    assert "你是 AiMemo 的主 ReAct agent" in prompt
    assert "AiMemo 项目规则" in prompt
    assert RUNTIME_AGENT_RULES in prompt


def test_planner_prompt_renamed_to_aimemo_and_asks_about_working_dir():
    prompt = _build_local_operator_planner_prompt("帮我做一个 RAG 项目")
    assert "你是 AiMemo 的 Local Operator" in prompt
    assert "Ai 记" not in prompt
    assert "没有明确指定目标目录" in prompt
    assert RUNTIME_AGENT_RULES in prompt
