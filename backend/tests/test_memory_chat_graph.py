from pathlib import Path

from sqlmodel import select

from app.agent.graphs.memory_chat.graph import build_memory_chat_graph, run_memory_chat_graph
from app.agent.graphs.memory_chat.nodes import RetrievalPlan
from app.agent.graphs.memory_chat.nodes import build_memory_chat_answer_system_prompt
from app.agent.graphs.memory_chat.nodes import build_agent_think_node
from app.agent.graphs.memory_chat.nodes import default_retrieval_planner
from app.agent.graphs.memory_chat.nodes import _build_model_messages
from app.agent.graphs.memory_chat.nodes import _llm_plan_agent_tool_action
from app.agent.graphs.memory_chat.nodes import _plan_agent_tool_action
from app.agent.graphs.memory_chat.nodes import _parse_elf_bubble_parts
from app.agent.graphs.memory_chat.nodes import _select_ready_task_step
from app.agent.graphs.memory_chat.nodes import _update_task_after_tool_observation
from app.agent.graphs.memory_chat.nodes import build_merge_prompt_context_node
from app.models.chat_message import ChatMessage
from app.models.long_term_memory import LongTermMemory
from app.rag.hashing import content_hash
from app.rag.search import NoteSearchResult
from app.schemas.conversation import ConversationCreate
from app.services.conversation_service import create_conversation
from app.services.chat_turn_service import get_chat_turn_state_history


def test_memory_chat_graph_direct_answer_persists_messages(
    session,
    session_factory,
    tmp_path: Path,
):
    conversation = create_conversation(session, ConversationCreate(title="直接回答"))

    def fake_answer(user_message, recent_messages, retrieved_chunks, needs_retrieval, retrieval_grade):
        assert user_message == "1+1 等于几？"
        assert recent_messages == []
        assert retrieved_chunks == []
        assert needs_retrieval is False
        assert retrieval_grade == "none"
        return "1+1 等于 2。"

    result = run_memory_chat_graph(
        conversation_id=conversation.id,
        user_message="1+1 等于几？",
        session_factory=session_factory,
        checkpoint_path=str(tmp_path / "checkpoints.db"),
        answer_generator=fake_answer,
    )

    messages = session.exec(select(ChatMessage).order_by(ChatMessage.id)).all()
    assert result["needs_retrieval"] is False
    assert result["context_l4_layer"]["level"] == 4
    assert result["context_l3_layer"]["level"] == 3
    assert result["context_l2_layer"]["level"] == 2
    assert result["context_l1_layer"]["level"] == 1
    assert result["context_l0_layer"]["level"] == 0
    assert result["context_conversation_window_layer"]["level"] == 1
    assert "L1 近期对话窗口" in result["prompt_context"]
    assert "L0 当前用户输入" in result["prompt_context"]
    assert "1+1 等于几？" in result["prompt_context"]
    assert "本轮未查询个人知识库" in result["prompt_context"]
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[0].content == "1+1 等于几？"
    assert messages[1].content == "1+1 等于 2。"
    assert messages[1].parent_id == messages[0].id
    assert messages[0].checkpoint_id
    assert messages[0].checkpoint_id == messages[1].checkpoint_id


def test_chat_turn_state_history_reads_langgraph_checkpoints(
    session,
    session_factory,
    tmp_path: Path,
):
    """Graph 调试接口应能读取 LangGraph 原生 checkpoint state history。"""

    conversation = create_conversation(session, ConversationCreate(title="checkpoint history"))
    checkpoint_path = tmp_path / "checkpoints.db"

    def fake_answer(user_message, recent_messages, retrieved_chunks, needs_retrieval, retrieval_grade):
        return "你好，checkpoint。"

    result = run_memory_chat_graph(
        conversation_id=conversation.id,
        user_message="测试 checkpoint history",
        session_factory=session_factory,
        checkpoint_path=str(checkpoint_path),
        answer_generator=fake_answer,
    )

    from app.models.chat_turn import ChatTurn

    turn = ChatTurn(
        conversation_id=conversation.id,
        thread_id=f"conversation:{conversation.id}",
        checkpoint_id=result["graph_checkpoint_id"],
    )
    session.add(turn)
    session.commit()
    session.refresh(turn)

    history = get_chat_turn_state_history(
        session,
        conversation_id=conversation.id,
        turn_id=turn.id or 0,
        checkpoint_path=str(checkpoint_path),
        limit=20,
    )

    assert history.thread_id == f"conversation:{conversation.id}"
    assert len(history.states) > 0
    assert history.states[0].checkpoint_id
    assert any("user_message" in snapshot.values for snapshot in history.states)


def test_memory_chat_graph_main_flow_is_flat_context_worker_graph(session_factory):
    graph = build_memory_chat_graph(session_factory=session_factory)
    mermaid = graph.compile().get_graph().draw_mermaid()

    assert "load_turn_state" in mermaid
    assert "dispatch_context_workers" in mermaid
    assert "build_l3_retrieved_memory" in mermaid
    assert "build_current_conversation_window" in mermaid
    assert "agent_think" in mermaid
    assert "select_tool" in mermaid
    assert "check_tool_policy" in mermaid
    assert "run_read_tool" in mermaid
    assert "run_write_tool" in mermaid
    assert "run_exec_tool" in mermaid
    assert "observe_tool_result" in mermaid
    assert "build_local_operator_context" not in mermaid
    assert "merge_prompt_context" in mermaid
    assert "generate_elf_bubble_answer" in mermaid
    assert "plan_retrieval" not in mermaid
    assert "retrieve_notes" not in mermaid
    assert "grade_retrieval" not in mermaid


def test_contextual_write_confirmation_uses_previous_assistant_draft():
    """用户确认“直接保存”时，应从上一轮 assistant 中补齐路径和正文。"""

    action = _plan_agent_tool_action(
        {
            "user_message": "我希望你直接保存到一个具体的文件",
            "recent_messages": [
                {
                    "id": 1,
                    "role": "assistant",
                    "content": (
                        "看看你一边推进 Ai 记，一边解决 Zenoh 迁移难题，真的很专注。\n\n"
                        "这段心里话已经准备好写入该目录了。你希望我直接把它保存成一个具体的文件"
                        "（比如 E:\\test\\message_to_jiaxuan.txt），还是你想先看看完整正文？"
                    ),
                    "token_count": 80,
                }
            ],
        }
    )

    assert action is not None
    assert action["tool_name"] == "write_file"
    assert action["arguments"]["path"] == "E:\\test\\message_to_jiaxuan.txt"
    assert "Ai 记" in action["arguments"]["content"]
    assert "保存成一个具体的文件" not in action["arguments"]["content"]


def test_contextual_write_confirmation_allows_short_real_draft():
    """短正文也可以写入，不能因为字数少就退回口头回答。"""

    action = _plan_agent_tool_action(
        {
            "user_message": "我希望你直接保存到一个具体的文件",
            "recent_messages": [
                {
                    "id": 1,
                    "role": "assistant",
                    "content": (
                        "家炫，你很专注，也很有创造力。\n\n"
                        "这段心里话已经准备好写入该目录了。你希望我直接把它保存成一个具体的文件"
                        "（比如 E:\\test\\message_to_jiaxuan.txt），还是你想先看看完整正文？"
                    ),
                    "token_count": 50,
                }
            ],
        }
    )

    assert action is not None
    assert action["tool_name"] == "write_file"
    assert action["arguments"]["content"] == "家炫，你很专注，也很有创造力。"


def test_contextual_write_confirmation_can_choose_filename_from_previous_directory():
    """用户让 agent 自己取文件名时，应沿用上一轮目录和正文执行写入。"""

    action = _plan_agent_tool_action(
        {
            "user_message": "你自己取一个文件名吧",
            "recent_messages": [
                {
                    "id": 1,
                    "role": "assistant",
                    "content": (
                        "我确认 E:/test/ 这个目录可以作为保存位置。\n\n"
                        "家炫，我想对你说：你对 Ai 记的推进非常认真，也很愿意把复杂系统拆开想清楚。"
                        "你不是只想做一个能跑的工具，而是在一点点塑造一个有温度的长期伙伴。\n\n"
                        "如果你愿意，我可以帮你自己取一个文件名并写进去。"
                    ),
                    "token_count": 90,
                }
            ],
        }
    )

    assert action is not None
    assert action["tool_name"] == "write_file"
    assert action["arguments"]["path"] == "E:/test/memo_elf_letter.md"
    assert "我想对你说" in action["arguments"]["content"]
    assert "取一个文件名" not in action["arguments"]["content"]


def test_new_html_write_request_does_not_reuse_contextual_markdown_filename():
    """新的 HTML 写入请求不能被“随便文件名”误判成上一轮 Markdown 草稿确认。"""

    action = _plan_agent_tool_action(
        {
            "user_message": "在E盘的test目录下面，写一个好看的html网页给我，随便取一个文件名即可",
            "recent_messages": [
                {
                    "id": 1,
                    "role": "assistant",
                    "content": (
                        "我确认 E:/test/ 这个目录可以作为保存位置。\n\n"
                        "家炫，我想对你说：这是上一轮准备写入 Markdown 的正文。"
                    ),
                    "token_count": 60,
                }
            ],
        }
    )

    assert action is None or not str(action.get("arguments", {}).get("path", "")).endswith(".md")


def test_contextual_default_filename_uses_html_extension_when_context_mentions_html():
    """确实需要上下文补文件名时，HTML 语义应补 .html 而不是 .md。"""

    action = _plan_agent_tool_action(
        {
            "user_message": "你自己取一个文件名吧",
            "recent_messages": [
                {
                    "id": 1,
                    "role": "assistant",
                    "content": (
                        "我确认 E:/test/ 这个目录可以作为保存位置。\n\n"
                        "<!doctype html><html><body><h1>Memo</h1></body></html>"
                    ),
                    "token_count": 60,
                }
            ],
        }
    )

    assert action is not None
    assert action["arguments"]["path"] == "E:/test/index.html"


def test_turn_messages_are_appended_to_final_model_messages():
    """最终模型输入应同时包含金字塔上下文和本轮 graph 消息流。"""

    messages = _build_model_messages(
        "system",
        "pyramid context",
        [
            {"role": "user", "content": "帮我写文件", "name": "current_user_input", "tool_call_id": None},
            {"role": "assistant", "content": "需要调用 write_file。", "name": "agent_think", "tool_call_id": None},
            {"role": "tool", "content": '{"ok":true}', "name": "write_file", "tool_call_id": "tool-1-write_file"},
        ],
    )

    assert [message.type for message in messages] == ["system", "human", "human", "ai", "human"]
    assert messages[1].content == "pyramid context"
    assert messages[-1].content == '[tool:write_file]\n{"ok":true}'


def test_agent_think_continues_existing_tool_queue_after_observation():
    """工具队列未执行完时，agent_think 不能因为已有 observation 就提前最终回答。"""

    node = build_agent_think_node()
    state = node(
        {
            "agent_loop_count": 1,
            "planned_tool_actions": [
                {
                    "tool_call_id": "tool-2-write_file",
                    "tool_name": "write_file",
                    "arguments": {
                        "path": "E:/test/memo_elf_letter.md",
                        "content": "家炫，我想对你说：继续认真做下去。",
                        "overwrite": False,
                    },
                    "reason": "创建用户要求的新文件。",
                }
            ],
            "tool_observations": [
                {
                    "tool_call_id": "tool-1-get_file_info",
                    "tool_name": "get_file_info",
                    "arguments": {"path": "E:/test/memo_elf_letter.md"},
                    "ok": False,
                    "error_code": "PATH_NOT_FOUND",
                    "message": "目标文件不存在。",
                }
            ],
            "tool_budget": 4,
            "turn_messages": [],
            "thought_events": [],
        }
    )

    assert state["agent_decision"]["type"] == "tool_call"
    assert state["planned_tool_actions"][0]["tool_name"] == "write_file"


def test_agent_think_builds_dynamic_task_from_planner(monkeypatch):
    """agent_think 应先生成 Task Plan，再从 ready step 派生工具调用。"""

    class FakePlannerModel:
        def invoke(self, messages):
            class Response:
                content = (
                    '{"needs_task":true,"goal":"读取配置文件",'
                    '"steps":[{"id":"read_config","kind":"tool","description":"读取 config",'
                    '"tool_name":"read_file","arguments":{"path":"E:/test/config.json"},"dependencies":[]}]}'
                )

            return Response()

    monkeypatch.setattr("app.agent.graphs.memory_chat.nodes.get_planner_chat_model", lambda: FakePlannerModel())

    state = build_agent_think_node()(
        {
            "user_message": "读取 E:/test/config.json，把 timeout 改成 30，然后保存回去",
            "recent_messages": [
                {
                    "id": 1,
                    "role": "assistant",
                    "content": "上一轮我写了 rust_hello.rs。",
                    "token_count": 20,
                }
            ],
            "prompt_context": "## L1 history\nassistant: 上一轮我写了 rust_hello.rs。\n\n## L0 current\n读取配置",
            "turn_messages": [],
            "tool_observations": [],
            "planned_tool_actions": [],
            "tool_budget": 4,
            "thought_events": [],
        }
    )

    assert state["task"]["goal"] == "读取配置文件"
    assert state["planned_tool_actions"][0]["tool_name"] == "read_file"
    assert state["planned_tool_actions"][0]["arguments"]["path"] == "E:/test/config.json"
    assert state["planned_tool_actions"][0]["task_boundary"] == "new_task"


def test_agent_think_consumes_reasoning_step_before_next_tool(monkeypatch):
    """reasoning step 应在 agent_think 内部完成，并把 content_ref 传给后续 write_file。"""

    class FakeAnswerModel:
        def invoke(self, messages):
            class Response:
                content = '{"timeout":30}\n'

            return Response()

    monkeypatch.setattr("app.agent.graphs.memory_chat.nodes.get_agent_chat_model", lambda: FakeAnswerModel())

    state = build_agent_think_node()(
        {
            "user_message": "把配置保存回去",
            "task": {
                "id": "task-test",
                "goal": "修改配置",
                "status": "READY",
                "plan_version": 1,
                "current_step_id": None,
                "world_state": {
                    "generated_outputs": {},
                    "observations": [],
                    "failures": [],
                    "read_files": {},
                    "written_files": {},
                    "known_files": {},
                    "approvals": [],
                },
                "steps": [
                    {
                        "id": "prepare_content",
                        "kind": "reasoning",
                        "description": "生成新配置",
                        "arguments": {},
                        "dependencies": [],
                        "status": "PENDING",
                        "retry_count": 0,
                    },
                    {
                        "id": "write_config",
                        "kind": "tool",
                        "description": "写回配置",
                        "tool_name": "write_file",
                        "arguments": {
                            "path": "E:/test/config.json",
                            "content_ref": "prepare_content",
                            "overwrite": True,
                        },
                        "dependencies": ["prepare_content"],
                        "status": "PENDING",
                        "retry_count": 0,
                    },
                ],
                "execution_history": [],
                "replan_count": 0,
            },
            "planned_tool_actions": [],
            "tool_observations": [],
            "turn_messages": [],
            "tool_budget": 4,
            "thought_events": [],
        }
    )

    assert state["task"]["world_state"]["generated_outputs"]["prepare_content"]["content"] == '{"timeout":30}\n'
    assert state["planned_tool_actions"][0]["tool_name"] == "write_file"
    assert state["planned_tool_actions"][0]["arguments"]["content"] == '{"timeout":30}\n'
    assert state["task"]["steps"][0]["status"] == "COMPLETED"


def test_agent_think_does_not_replan_completed_dynamic_task(monkeypatch):
    """已完成的动态任务必须进入最终回答，不能按同一条 L0 输入重新规划。"""

    class FailingPlannerModel:
        def invoke(self, messages):  # pragma: no cover - 调到这里就说明终态判断失效
            raise AssertionError("completed task should not be replanned")

    monkeypatch.setattr("app.agent.graphs.memory_chat.nodes.get_planner_chat_model", lambda: FailingPlannerModel())

    state = build_agent_think_node()(
        {
            "user_message": "读取 E:/test/config.json，把 timeout 改成 30，然后保存回去",
            "task": {
                "id": "task-config",
                "goal": "修改配置",
                "status": "COMPLETED",
                "plan_version": 1,
                "current_step_id": None,
                "world_state": {
                    "generated_outputs": {"prepare_content": {"content": '{"timeout":30}\n'}},
                    "observations": [],
                    "failures": [],
                    "read_files": {},
                    "written_files": {"E:/test/config.json": {"bytes_written": 15}},
                    "known_files": {},
                    "approvals": [],
                },
                "steps": [
                    {"id": "read_config", "kind": "tool", "status": "COMPLETED", "dependencies": []},
                    {"id": "prepare_content", "kind": "reasoning", "status": "COMPLETED", "dependencies": ["read_config"]},
                    {"id": "write_config", "kind": "tool", "status": "COMPLETED", "dependencies": ["prepare_content"]},
                ],
                "execution_history": [],
                "replan_count": 0,
            },
            "planned_tool_actions": [],
            "tool_observations": [],
            "turn_messages": [],
            "tool_budget": 4,
            "thought_events": [],
        }
    )

    assert state["agent_decision"]["type"] == "final_answer"
    assert "planned_tool_actions" not in state


def test_tool_observation_completes_source_step_once():
    """工具 observation 应按 source_step_id 推进对应 step，完成后不能再次被选中。"""

    task = {
        "id": "task-config",
        "goal": "修改配置",
        "status": "RUNNING",
        "plan_version": 1,
        "current_step_id": "write_config",
        "world_state": {
            "generated_outputs": {},
            "observations": [],
            "failures": [],
            "read_files": {},
            "written_files": {},
            "known_files": {},
            "approvals": [],
        },
        "steps": [
            {"id": "read_config", "kind": "tool", "status": "COMPLETED", "dependencies": []},
            {"id": "write_config", "kind": "tool", "status": "EXECUTING", "dependencies": ["read_config"]},
        ],
        "execution_history": [],
        "replan_count": 0,
    }
    updated = _update_task_after_tool_observation(
        {
            "task": task,
            "pending_tool_action": {
                "tool_call_id": "tool-1-write_file",
                "tool_name": "write_file",
                "source_step_id": "write_config",
            },
        },
        {
            "tool_call_id": "tool-1-write_file",
            "tool_name": "write_file",
            "arguments": {"path": "E:/test/config.json"},
            "ok": True,
            "data": {"path": "E:/test/config.json", "bytes_written": 15},
            "message": "写入完成。",
            "blocked": False,
        },
    )

    assert updated["status"] == "COMPLETED"
    assert updated["steps"][1]["status"] == "COMPLETED"
    assert "E:/test/config.json" in updated["world_state"]["written_files"]
    assert _select_ready_task_step(updated) is None


def test_agent_tool_planner_cleans_markdown_backticks_from_path(monkeypatch):
    """LLM planner 返回 Markdown 反引号包裹路径时，不能把反引号写进真实路径。"""

    class FakeModel:
        def invoke(self, messages):
            class Response:
                content = (
                    '{"needs_tool":true,"tool_name":"write_file",'
                    '"arguments":{"path":"E:/test`/memo.md","content":"真实正文","overwrite":false},'
                    '"confidence":0.9,"reason":"写入文件"}'
                )

            return Response()

    monkeypatch.setattr("app.agent.graphs.memory_chat.nodes.get_planner_chat_model", lambda: FakeModel())

    action = _llm_plan_agent_tool_action(
        {
            "user_message": "写入文件",
            "prompt_context": "",
            "turn_messages": [],
            "tool_observations": [],
        }
    )

    assert action is not None
    assert action["arguments"]["path"] == "E:/test/memo.md"


def test_agent_tool_planner_accepts_exec_command(monkeypatch):
    """主 agent planner 应允许 exec_command 进入工具循环。"""

    class FakeModel:
        def invoke(self, messages):
            class Response:
                content = (
                    '{"needs_tool":true,"tool_name":"exec_command",'
                    '"arguments":{"command":"git status --short","cwd":".","timeout_ms":30000,"max_output_bytes":65536},'
                    '"confidence":0.9,"reason":"查看 git 状态"}'
                )

            return Response()

    monkeypatch.setattr("app.agent.graphs.memory_chat.nodes.get_planner_chat_model", lambda: FakeModel())

    action = _llm_plan_agent_tool_action(
        {
            "user_message": "帮我运行 git status",
            "prompt_context": "",
            "turn_messages": [],
            "tool_observations": [],
        }
    )

    assert action is not None
    assert action["tool_name"] == "exec_command"
    assert action["arguments"]["command"] == "git status --short"


def test_contextual_path_clean_removes_trailing_backtick():
    """上下文抽取目录时应移除尾部 Markdown 反引号。"""

    action = _plan_agent_tool_action(
        {
            "user_message": "你自己取一个文件名吧",
            "recent_messages": [
                {
                    "id": 1,
                    "role": "assistant",
                    "content": (
                        "我确认 `E:/test` 目录可以作为保存位置。\n\n"
                        "家炫，我想对你说：这次路径不要带反引号。"
                    ),
                    "token_count": 30,
                }
            ],
        }
    )

    assert action is not None
    assert action["arguments"]["path"].startswith("E:/test/")
    assert "`" not in action["arguments"]["path"]


def test_memory_chat_profile_question_uses_rule_planner_fast_path():
    plan = default_retrieval_planner("你觉得我是一个怎么样的人", [])

    assert plan.needs_retrieval is True
    assert plan.needs_query_rewrite is True
    assert plan.source == "rule_profile"
    assert "用户个人画像" in plan.retrieval_query


def test_memory_chat_answer_prompt_prefers_natural_profile_style():
    """回答提示词应避免把个人画像问题写成僵硬的检索报告。"""

    prompt = build_memory_chat_answer_system_prompt()

    assert "像熟悉用户的伙伴" in prompt
    assert "不要像检索报告或审计说明" in prompt
    assert "个人画像类问题" in prompt
    assert "不要把回答开头写成免责声明" in prompt
    assert "不暴露 graph、L0-L4、retrieval_grade、chunk、score" in prompt


def test_memory_chat_graph_retrieves_notes_when_needed(
    session,
    session_factory,
    tmp_path: Path,
):
    conversation = create_conversation(session, ConversationCreate(title="记忆查询"))
    retriever_calls: list[str] = []

    def fake_retriever(current_session, *, query: str, limit: int):
        assert current_session is not None
        assert limit == 5
        retriever_calls.append(query)
        return [
            NoteSearchResult(
                note_id=4,
                note_title="今天中午想吃炸鸡",
                chunk_id=10,
                chunk_index=0,
                content="今天中午我想点炸鸡吃",
                content_hash="hash",
                token_count=16,
                distance=0.1,
                score=0.9,
            )
        ]

    def fake_answer(user_message, recent_messages, retrieved_chunks, needs_retrieval, retrieval_grade):
        assert needs_retrieval is True
        assert retrieval_grade == "good"
        assert retrieved_chunks[0]["note_title"] == "今天中午想吃炸鸡"
        return "你之前说过中午想吃炸鸡。"

    result = run_memory_chat_graph(
        conversation_id=conversation.id,
        user_message="我之前说过想吃什么？",
        session_factory=session_factory,
        checkpoint_path=str(tmp_path / "checkpoints.db"),
        retriever=fake_retriever,
        answer_generator=fake_answer,
    )

    assert retriever_calls == ["我之前说过想吃什么？"]
    assert result["needs_retrieval"] is True
    assert result["retrieval_query"] == "我之前说过想吃什么？"
    assert result["retrieval_grade"] == "good"
    assert "L3 RAG 检索记忆" in result["prompt_context"]
    assert "今天中午我想点炸鸡吃" in result["prompt_context"]
    assert result["retrieved_chunks"][0]["content"] == "今天中午我想点炸鸡吃"


def test_memory_chat_graph_elf_bubble_mode_persists_joined_bubbles(
    session,
    session_factory,
    tmp_path: Path,
):
    conversation = create_conversation(session, ConversationCreate(title="精灵气泡"))

    def fake_bubbles(user_message, recent_messages, retrieved_chunks, needs_retrieval, retrieval_grade):
        assert user_message == "在吗"
        return [
            {"text": "我在呀。", "emoji": "happy"},
            {"text": "想聊什么都可以。", "emoji": "soft"},
        ]

    result = run_memory_chat_graph(
        conversation_id=conversation.id,
        user_message="在吗",
        session_factory=session_factory,
        checkpoint_path=str(tmp_path / "checkpoints.db"),
        bubble_answer_generator=fake_bubbles,
        answer_mode="elf_bubble",
    )

    messages = session.exec(select(ChatMessage).order_by(ChatMessage.id)).all()
    assert result["elf_bubble_answer_parts"][0]["emoji"] == "success_smile"
    assert result["assistant_answer"] == "我在呀。\n\n想聊什么都可以。"
    assert messages[-1].content == "我在呀。\n\n想聊什么都可以。"


def test_elf_bubble_parser_splits_obvious_emotion_shift():
    parts = _parse_elf_bubble_parts(
        '{"bubbles":[{"text":"当然开心呀，小刘！但是我也有点担心你今天太累了。","emoji":"happy"}]}'
    )

    assert [part["text"] for part in parts] == [
        "当然开心呀，小刘！",
        "但是我也有点担心你今天太累了。",
    ]
    assert [part["emoji"] for part in parts] == ["success_smile", "error_worried"]


def test_elf_bubble_parser_accepts_expanded_expression_emoji():
    parts = _parse_elf_bubble_parts(
        '{"bubbles":['
        '{"text":"这件事我会认真看。","emoji":"serious"},'
        '{"text":"不过你也别急，我们慢慢来。","emoji":"encouraging"},'
        '{"text":"诶，这个结果有点出乎意料。","emoji":"surprised"},'
        '{"text":"哼，才不是特意帮你的哦。","emoji":"tsundere_pout"},'
        '{"text":"大成功，漂亮完成。","emoji":"sparkle_success"}'
        "]}"
    )

    assert [part["emoji"] for part in parts] == [
        "serious",
        "encouraging",
        "surprised",
        "tsundere_pout",
        "sparkle_success",
    ]


def test_memory_chat_graph_uses_planned_retrieval_query(
    session,
    session_factory,
    tmp_path: Path,
):
    conversation = create_conversation(session, ConversationCreate(title="改写查询"))
    retriever_calls: list[str] = []

    def fake_planner(user_message, recent_messages):
        assert user_message == "那个吃的是什么来着？"
        return RetrievalPlan(
            intent="rag",
            needs_retrieval=True,
            needs_query_rewrite=True,
            retrieval_query="用户之前提到想吃的食物",
            confidence=0.8,
            reason="用户使用了指代词，需要改写查询。",
        )

    def fake_retriever(current_session, *, query: str, limit: int):
        retriever_calls.append(query)
        return [
            NoteSearchResult(
                note_id=4,
                note_title="今天中午想吃炸鸡",
                chunk_id=10,
                chunk_index=0,
                content="今天中午我想点炸鸡吃",
                content_hash="hash",
                token_count=16,
                distance=0.7,
                score=0.45,
            )
        ]

    def fake_answer(user_message, recent_messages, retrieved_chunks, needs_retrieval, retrieval_grade):
        assert retrieval_grade == "weak"
        return "这条记忆可能相关：你提到过炸鸡。"

    result = run_memory_chat_graph(
        conversation_id=conversation.id,
        user_message="那个吃的是什么来着？",
        session_factory=session_factory,
        checkpoint_path=str(tmp_path / "checkpoints.db"),
        planner=fake_planner,
        retriever=fake_retriever,
        answer_generator=fake_answer,
    )

    assert retriever_calls == ["用户之前提到想吃的食物"]
    assert result["needs_query_rewrite"] is True
    assert result["retrieval_query"] == "用户之前提到想吃的食物"
    assert result["retrieval_grade"] == "weak"


def test_memory_chat_graph_resume_after_answer_does_not_regenerate(
    session,
    session_factory,
    tmp_path: Path,
):
    conversation = create_conversation(session, ConversationCreate(title="恢复测试"))
    calls: list[str] = []
    checkpoint_path = tmp_path / "checkpoints.db"

    def fake_answer(user_message, recent_messages, retrieved_chunks, needs_retrieval, retrieval_grade):
        calls.append(user_message)
        return "这是已经生成过的回答。"

    run_memory_chat_graph(
        conversation_id=conversation.id,
        user_message="你好",
        session_factory=session_factory,
        checkpoint_path=str(checkpoint_path),
        answer_generator=fake_answer,
        interrupt_after=["generate_answer"],
    )

    assert calls == ["你好"]
    assert session.exec(select(ChatMessage)).all() == []

    result = run_memory_chat_graph(
        conversation_id=conversation.id,
        user_message="你好",
        session_factory=session_factory,
        checkpoint_path=str(checkpoint_path),
        answer_generator=fake_answer,
    )

    messages = session.exec(select(ChatMessage).order_by(ChatMessage.id)).all()
    assert calls == ["你好"]
    assert result["assistant_answer"] == "这是已经生成过的回答。"
    assert [message.role for message in messages] == ["user", "assistant"]


def test_memory_chat_graph_includes_l4_core_memory_in_prompt_context(
    session,
    session_factory,
    tmp_path: Path,
):
    conversation = create_conversation(session, ConversationCreate(title="核心记忆上下文"))
    session.add(
        LongTermMemory(
            level=4,
            category="preference",
            content="用户不吃香菜。",
            summary="不吃香菜",
            importance=0.95,
            confidence=0.9,
            content_hash=content_hash("preference:用户不吃香菜。"),
        )
    )
    session.commit()

    def fake_answer(user_message, recent_messages, retrieved_chunks, needs_retrieval, retrieval_grade):
        return "我会记得你不吃香菜。"

    result = run_memory_chat_graph(
        conversation_id=conversation.id,
        user_message="以后点菜注意一下",
        session_factory=session_factory,
        checkpoint_path=str(tmp_path / "checkpoints.db"),
        planner=lambda user_message, recent_messages: RetrievalPlan(
            intent="direct",
            needs_retrieval=False,
            needs_query_rewrite=False,
            retrieval_query="",
            confidence=1.0,
            reason="测试直接回答。",
        ),
        answer_generator=fake_answer,
    )

    assert "L4 核心长期记忆" in result["prompt_context"]
    assert "用户不吃香菜。" in result["prompt_context"]
