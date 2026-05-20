from pathlib import Path

import pytest
from sqlmodel import select

from app.agent.graphs.memory_chat.nodes import _configured_local_operator_workspace_roots
from app.agent.graphs.memory_chat.nodes import _default_local_operator_workspace_roots
from app.agent.graphs.local_operator.nodes import _build_local_operator_planner_prompt
from app.agent.graphs.local_operator.graph import run_local_operator_graph
from app.core.config import settings
from app.local_operator.filesystem import LocalFilesystemService
from app.local_operator.policy import LocalOperatorPolicy
from app.models.agent_operation import AgentOperation


def test_read_file_blocks_path_escape(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    service = LocalFilesystemService(LocalOperatorPolicy.from_roots([str(workspace)]))

    result = service.read_file(str(outside))

    assert result.ok is False
    assert result.error_code == "PATH_OUTSIDE_WORKSPACE"


def test_read_file_blocks_sensitive_file(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".env").write_text("DASHSCOPE_API_KEY=secret", encoding="utf-8")
    service = LocalFilesystemService(LocalOperatorPolicy.from_roots([str(workspace)]))

    result = service.read_file(".env")

    assert result.ok is False
    assert result.error_code == "SENSITIVE_FILE_BLOCKED"
    assert result.blocked is True


def test_read_file_supports_line_range(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.txt").write_text("一\n二\n三\n四", encoding="utf-8")
    service = LocalFilesystemService(LocalOperatorPolicy.from_roots([str(workspace)]))

    result = service.read_file("note.txt", start_line=2, end_line=3)

    assert result.ok is True
    assert result.data["line_start"] == 2
    assert result.data["line_end"] == 3
    assert result.data["content"] == "二\n三"
    assert "     2\t二" in result.data["numbered_content"]


def test_read_file_strips_utf8_bom_and_normalizes_crlf(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "note.txt").write_bytes(b"\xef\xbb\xbfalpha\r\nbeta\r\n")
    service = LocalFilesystemService(LocalOperatorPolicy.from_roots([str(workspace)]))

    result = service.read_file("note.txt")

    assert result.ok is True
    assert result.data["content"] == "alpha\nbeta\n"
    assert result.data["total_bytes"] == 16
    assert result.data["modified_at"]


def test_read_file_supports_utf16le_bom(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "utf16.txt").write_bytes(b"\xff\xfe" + "你好\r\n世界".encode("utf-16-le"))
    service = LocalFilesystemService(LocalOperatorPolicy.from_roots([str(workspace)]))

    result = service.read_file("utf16.txt")

    assert result.ok is True
    assert result.data["content"] == "你好\n世界"


def test_read_file_rejects_null_byte_path(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = LocalFilesystemService(LocalOperatorPolicy.from_roots([str(workspace)]))

    result = service.read_file("bad\x00name.txt")

    assert result.ok is False
    assert result.error_code == "PATH_CONTAINS_NULL_BYTE"
    assert result.blocked is True


def test_read_file_blocks_dangerous_device_path(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    service = LocalFilesystemService(LocalOperatorPolicy.from_roots([str(workspace)]))

    result = service.read_file("/dev/zero")

    assert result.ok is False
    assert result.error_code == "DEVICE_PATH_BLOCKED"
    assert result.blocked is True


@pytest.mark.skipif(not str(Path.home()).startswith(str(Path.home().anchor)), reason="requires normal home path")
def test_policy_supports_home_expansion():
    home = Path.home().resolve()
    policy = LocalOperatorPolicy.from_roots([str(home)])

    resolved = policy.resolve_authorized_path("~")

    assert resolved == home


@pytest.mark.skipif(not (Path("C:/").exists()), reason="Windows drive path conversion only applies on Windows")
def test_policy_supports_posix_style_windows_drive_path():
    policy = LocalOperatorPolicy.from_roots(["C:/"])

    resolved = policy.resolve_authorized_path("/c/")

    assert resolved == Path("C:/").resolve()


@pytest.mark.skipif(not (Path("C:/").exists()), reason="Windows fixed drive path only applies on Windows")
def test_policy_allows_absolute_path_when_drive_root_is_authorized(tmp_path: Path):
    target = tmp_path / "readme.txt"
    target.write_text("hello from absolute path", encoding="utf-8")
    policy = LocalOperatorPolicy.from_roots([str(Path(target.anchor).resolve())])

    resolved = policy.resolve_authorized_path(str(target))

    assert resolved == target.resolve()


def test_read_file_large_file_allows_small_range(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    big_file = workspace / "large.txt"
    with big_file.open("w", encoding="utf-8", newline="\n") as file:
        for index in range(700_000):
            file.write(f"line-{index}\n")
    service = LocalFilesystemService(LocalOperatorPolicy.from_roots([str(workspace)]))

    result = service.read_file("large.txt", start_line=700_000, end_line=700_000, max_bytes=128)

    assert result.ok is True
    assert result.data["line_start"] == 700_000
    assert result.data["line_end"] == 700_000
    assert result.data["content"] == "line-699999"


def test_missing_path_returns_nearby_suggestion(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "memory-chat-graph.md").write_text("graph", encoding="utf-8")
    service = LocalFilesystemService(LocalOperatorPolicy.from_roots([str(workspace)]))

    result = service.read_file("memory-chat.md")

    assert result.ok is False
    assert result.error_code == "PATH_NOT_FOUND"
    assert "memory-chat-graph.md" in result.message


def test_search_text_finds_matches(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "a.py").write_text("alpha\nmemory_key = 'x'\nomega", encoding="utf-8")
    service = LocalFilesystemService(LocalOperatorPolicy.from_roots([str(workspace)]))

    result = service.search_text(".", query="memory_key", include_glob="*.py")

    assert result.ok is True
    assert result.data["match_count"] == 1
    assert result.data["matches"][0]["relative_path"] == "a.py"
    assert result.data["matches"][0]["line"] == 2


def test_local_operator_graph_reads_file_and_writes_audit(
    session,
    session_factory,
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / "hello.txt").write_text("你好，Ai 记。", encoding="utf-8")

    result = run_local_operator_graph(
        session_factory=session_factory,
        conversation_id=7,
        turn_id=11,
        user_input="帮我读取 hello.txt 这个文件",
        workspace_roots=[str(workspace)],
    )

    operations = session.exec(select(AgentOperation)).all()
    assert result["need_local_read"] is True
    assert "你好，Ai 记。" in result["final_answer"]
    assert len(operations) == 1
    assert operations[0].conversation_id == 7
    assert operations[0].turn_id == 11
    assert operations[0].operation_type == "read"
    assert operations[0].status == "completed"
    assert operations[0].tool_name == "read_file"


def test_local_operator_graph_detects_project_existence_question(
    session,
    session_factory,
    tmp_path: Path,
):
    workspace = tmp_path / "Ai记"
    workspace.mkdir()

    result = run_local_operator_graph(
        session_factory=session_factory,
        conversation_id=8,
        turn_id=12,
        user_input="我当前电脑有没有 Ai记 的项目？",
        workspace_roots=[str(workspace)],
    )

    operations = session.exec(select(AgentOperation).where(AgentOperation.conversation_id == 8)).all()
    assert result["need_local_read"] is True
    assert "Ai记" in result["final_answer"]
    assert len(operations) == 1
    assert operations[0].tool_name == "search_files"


def test_local_operator_graph_searches_home_for_whole_computer_project_question(
    session,
    session_factory,
    tmp_path: Path,
):
    home = tmp_path / "home"
    project = home / "AiMemo"
    project.mkdir(parents=True)

    result = run_local_operator_graph(
        session_factory=session_factory,
        conversation_id=18,
        turn_id=22,
        user_input="我当前电脑有没有 AiMemo 这个项目？",
        workspace_roots=[str(tmp_path / "repo"), str(home)],
    )

    operations = session.exec(select(AgentOperation).where(AgentOperation.conversation_id == 18)).all()
    assert result["need_local_read"] is True
    assert "AiMemo" in result["final_answer"]
    assert len(operations) == 1
    assert operations[0].tool_name == "search_files"


def test_local_operator_graph_uses_llm_planner_for_ambiguous_local_request(
    session,
    session_factory,
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    planner_calls: list[str] = []

    def fake_planner(user_input: str):
        planner_calls.append(user_input)
        return {
            "tool_name": "get_file_info",
            "arguments": {"path": "."},
            "reason": "测试 planner 判断为本地项目确认。",
        }

    result = run_local_operator_graph(
        session_factory=session_factory,
        conversation_id=9,
        turn_id=13,
        user_input="帮我确认这个仓库还在工作区里吗？",
        workspace_roots=[str(workspace)],
        planner=fake_planner,
    )

    operations = session.exec(select(AgentOperation).where(AgentOperation.conversation_id == 9)).all()
    assert planner_calls == ["帮我确认这个仓库还在工作区里吗？"]
    assert result["need_local_read"] is True
    assert operations[0].tool_name == "get_file_info"


def test_local_operator_graph_skips_planner_for_plain_chat(
    session_factory,
    tmp_path: Path,
):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    planner_calls: list[str] = []

    def fake_planner(user_input: str):
        planner_calls.append(user_input)
        return {
            "tool_name": "get_file_info",
            "arguments": {"path": "."},
            "reason": "不应该被调用。",
        }

    result = run_local_operator_graph(
        session_factory=session_factory,
        conversation_id=10,
        turn_id=14,
        user_input="你觉得我今天状态怎么样？",
        workspace_roots=[str(workspace)],
        planner=fake_planner,
    )

    assert planner_calls == []
    assert result["need_local_read"] is False


def test_default_local_operator_workspace_roots_include_repo_and_home():
    roots = [Path(root) for root in _default_local_operator_workspace_roots()]

    assert Path.home().resolve() in roots
    assert any((root / "backend").exists() and (root / "frontend").exists() for root in roots)


@pytest.mark.skipif(not (Path("C:/").exists()), reason="Windows fixed drive path only applies on Windows")
def test_default_local_operator_workspace_roots_include_fixed_drive_roots():
    roots = [Path(root).resolve() for root in _default_local_operator_workspace_roots()]

    assert Path("C:/").resolve() in roots


def test_local_operator_planner_prompt_does_not_preemptively_refuse_absolute_paths():
    prompt = _build_local_operator_planner_prompt("帮我看看 C:\\Windows\\Logs\\setup.log")

    assert "用户明确给出的绝对路径可以原样传给工具" in prompt
    assert "不能凭空声称" in prompt


def test_configured_local_operator_workspace_roots_parse_semicolon_and_comma(monkeypatch):
    monkeypatch.setattr(settings, "local_operator_workspace_roots", "D:\\资料;~/Documents,E:\\Projects")

    roots = _configured_local_operator_workspace_roots()

    assert roots == ["D:\\资料", "~/Documents", "E:\\Projects"]
