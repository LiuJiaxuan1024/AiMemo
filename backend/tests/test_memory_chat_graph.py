from pathlib import Path

from sqlmodel import select

from app.agent.graphs.memory_chat.graph import build_memory_chat_graph, run_memory_chat_graph
from app.agent.graphs.memory_chat.graph import _resolve_graph_input_for_turn
from app.agent.checkpoints import get_sqlite_checkpointer
from app.agent.graphs.memory_chat.nodes import RetrievalPlan
from app.agent.graphs.memory_chat.nodes import build_memory_chat_answer_system_prompt
from app.agent.graphs.memory_chat.nodes import build_agent_think_node
from app.agent.graphs.memory_chat.nodes import build_generate_answer_node
from app.agent.graphs.memory_chat.nodes import build_plan_task_node
from app.agent.graphs.memory_chat.nodes import build_verify_goal_node
from app.agent.graphs.memory_chat.nodes import build_load_turn_state_node
from app.agent.graphs.memory_chat.nodes import default_retrieval_planner
from app.agent.graphs.memory_chat.nodes import _build_agent_tool_planner_prompt
from app.agent.graphs.memory_chat.nodes import _build_model_messages
from app.agent.graphs.memory_chat.nodes import _llm_plan_agent_tool_action
from app.agent.graphs.memory_chat.nodes import _plan_agent_tool_action
from app.agent.graphs.memory_chat.nodes import _parse_elf_bubble_parts
from app.agent.graphs.memory_chat.nodes import _select_ready_task_step
from app.agent.graphs.memory_chat.nodes import _update_task_after_tool_observation
from app.agent.graphs.memory_chat.nodes import _evaluate_world_status
from app.agent.graphs.memory_chat.nodes import _replan_task_from_world_status
from app.agent.graphs.memory_chat.nodes import _llm_replan_dynamic_task
from app.agent.graphs.memory_chat.nodes import build_merge_prompt_context_node
from app.agent.graphs.memory_chat.nodes import _tool_observations_to_context
from app.models.chat_message import ChatMessage
from app.models.long_term_memory import LongTermMemory
from app.models.conversation import Conversation
from app.rag.hashing import content_hash
from app.rag.search import NoteSearchResult
from app.schemas.conversation import ConversationCreate
from app.services.conversation_service import create_conversation
from app.services.chat_turn_service import initial_node_statuses
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


def test_graph_input_resumes_same_turn_checkpoint(session_factory, tmp_path: Path):
    """同一条业务消息的中断现场可以继续 resume，不应被误判为新任务。"""

    graph = build_memory_chat_graph(session_factory=session_factory)
    with get_sqlite_checkpointer(str(tmp_path / "same-turn.db")) as checkpointer:
        app = graph.compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": "test-same-turn"}}
        app.update_state(
            config,
            {
                "conversation_id": 1,
                "user_message": "旧问题",
                "user_message_id": 11,
                "assistant_message_id": 12,
                "planned_tool_actions": [{"tool_name": "read_file"}],
            },
            as_node="agent_think",
        )
        snapshot = app.get_state(config)

        graph_input = _resolve_graph_input_for_turn(
            app,
            config,
            snapshot=snapshot,
            conversation_id=1,
            user_message="旧问题",
            answer_mode="text",
            user_message_id=11,
            assistant_message_id=12,
        )

    assert graph_input is None


def test_graph_input_expires_stale_checkpoint_for_new_message(session_factory, tmp_path: Path):
    """新用户消息到达时，不能继续旧 checkpoint 的 pending 工具队列。"""

    graph = build_memory_chat_graph(session_factory=session_factory)
    with get_sqlite_checkpointer(str(tmp_path / "new-turn.db")) as checkpointer:
        app = graph.compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": "test-new-turn"}}
        app.update_state(
            config,
            {
                "conversation_id": 1,
                "user_message": "读取 A",
                "user_message_id": 21,
                "assistant_message_id": 22,
                "planned_tool_actions": [{"tool_name": "read_file"}],
                "pending_tool_action": {"tool_name": "read_file"},
                "task": {"id": "task-old", "status": "RUNNING", "execution_history": []},
            },
            as_node="agent_think",
        )
        snapshot = app.get_state(config)

        graph_input = _resolve_graph_input_for_turn(
            app,
            config,
            snapshot=snapshot,
            conversation_id=1,
            user_message="写入 B",
            answer_mode="text",
            user_message_id=31,
            assistant_message_id=32,
        )
        expired = app.get_state(config)

    assert graph_input["user_message"] == "写入 B"
    assert expired.next == ()
    assert expired.values["planned_tool_actions"] == []
    assert expired.values["pending_tool_action"] is None
    assert expired.values["expired_task"]["status"] == "SUPERSEDED"
    assert expired.values["world_status"] == {}
    assert expired.values["task_boundary"]["type"] == "expired_stale_checkpoint"


def test_memory_chat_graph_main_flow_is_flat_context_worker_graph(session_factory):
    graph = build_memory_chat_graph(session_factory=session_factory)
    mermaid = graph.compile().get_graph().draw_mermaid()

    assert "load_turn_state" in mermaid
    assert "dispatch_context_workers" in mermaid
    assert "build_l3_retrieved_memory" in mermaid
    assert "build_current_conversation_window" in mermaid
    assert "plan_task" in mermaid
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


def test_initial_chat_turn_statuses_include_dynamic_tool_loop_nodes():
    """前端 graph 状态表应覆盖主图里的动态任务和 exec/verify 节点。"""

    statuses = initial_node_statuses()

    assert "plan_task" in statuses
    assert "run_exec_tool" in statuses
    assert "verify_goal" in statuses


def test_load_turn_state_restores_active_task_for_followup_confirmation(session, session_factory):
    """用户下一轮确认继续时，应恢复上一轮未完成的 conversation.active_task。"""

    conversation_read = create_conversation(session, ConversationCreate(title="继续任务"))
    conversation = session.get(Conversation, conversation_read.id)
    assert conversation is not None
    conversation.active_task = (
        '{"id":"task-rust","goal":"运行程序并返回结果","status":"RUNNING",'
        '"pending_steps":[{"id":"run","kind":"tool","description":"运行程序",'
        '"tool_name":"exec_command","arguments":{"command":"cargo run","cwd":"E:/demo"},'
        '"dependencies":[],"status":"PENDING"}],'
        '"completed_steps":[],"failed_steps":[],"steps":[],'
        '"world_state":{"cwd":null,"known_files":{},"read_files":{},"written_files":{},'
        '"generated_outputs":{},"observations":[],"failures":[],"replan_debug":[],"approvals":[]},'
        '"execution_history":[],"replan_count":0}'
    )
    session.add(conversation)
    session.add(
        ChatMessage(
            conversation_id=conversation_read.id,
            role="assistant",
            content="我需要修正配置后继续运行程序，才能把运行结果给你。",
            status="completed",
        )
    )
    session.commit()

    load_turn_state = build_load_turn_state_node(session_factory)
    state = load_turn_state(
        {
            "conversation_id": conversation_read.id,
            "user_message": "随便你，继续运行就行",
        }
    )

    assert state["task"]["id"] == "task-rust"
    assert state["task_boundary"]["type"] == "continuation"
    assert state["world_state"] == state["task"]["world_state"]
    assert state["task"]["execution_history"][-1]["type"] == "continued_in_new_turn"


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


def test_plan_task_builds_dynamic_task_then_agent_think_executes_step(monkeypatch):
    """plan_task 生成全局 Task，agent_think 只负责从 ready step 派生工具调用。"""

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

    base_state = {
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
    planned_state = build_plan_task_node()(base_state)
    state = build_agent_think_node()({**base_state, **planned_state})

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


def test_read_before_write_failure_replans_read_then_write_same_path(monkeypatch):
    """READ_BEFORE_WRITE_REQUIRED 必须恢复为同路径 read -> write，不能换路径绕过。"""

    class FailingPlannerModel:
        def invoke(self, messages):  # pragma: no cover - 确定性恢复应在 LLM 前完成
            raise AssertionError("read-before-write recovery should not call LLM replanner")

    monkeypatch.setattr("app.agent.graphs.memory_chat.nodes.get_planner_chat_model", lambda: FailingPlannerModel())

    task = {
        "id": "task-rust",
        "goal": "更新 Cargo 项目 main.rs",
        "status": "RUNNING",
        "plan_version": 1,
        "current_step_id": "write_code",
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
                "id": "write_code",
                "kind": "tool",
                "tool_name": "write_file",
                "arguments": {
                    "path": "E:/demo/random_numbers/src/main.rs",
                    "content": "fn main() { println!(\"hi\"); }\n",
                    "overwrite": True,
                },
                "dependencies": [],
                "status": "EXECUTING",
                "retry_count": 0,
            }
        ],
        "execution_history": [],
        "replan_count": 0,
    }
    failed_task = _update_task_after_tool_observation(
        {
            "task": task,
            "pending_tool_action": {
                "tool_call_id": "tool-1-write_file",
                "tool_name": "write_file",
                "source_step_id": "write_code",
            },
        },
        {
            "tool_call_id": "tool-1-write_file",
            "tool_name": "write_file",
            "arguments": {"path": "E:/demo/random_numbers/src/main.rs", "overwrite": True},
            "ok": False,
            "data": {},
            "error_code": "READ_BEFORE_WRITE_REQUIRED",
            "message": "覆盖已有文件前必须先读取或查看该文件。",
            "blocked": True,
        },
    )
    world_status = _evaluate_world_status(failed_task, failed_task["world_state"], {"user_message": "更新并运行"})
    replanned = _replan_task_from_world_status({"user_message": "更新并运行"}, failed_task, world_status)

    assert world_status["recovery_hint"] == "read_then_write_same_path"
    assert world_status["recovery_path"] == "E:/demo/random_numbers/src/main.rs"
    assert replanned is not None
    assert replanned["steps"][0]["tool_name"] == "read_file"
    assert replanned["steps"][0]["arguments"]["path"] == "E:/demo/random_numbers/src/main.rs"
    assert replanned["steps"][1]["tool_name"] == "write_file"
    assert replanned["steps"][1]["arguments"]["path"] == "E:/demo/random_numbers/src/main.rs"
    assert replanned["steps"][1]["dependencies"] == [replanned["steps"][0]["id"]]
    assert replanned["replan_count"] == 0


def test_failed_step_replan_inserts_recovery_before_remaining_pending_steps(monkeypatch):
    """失败 step 的恢复步骤必须插到队首，未执行的原步骤保留在队尾。"""

    class FailingPlannerModel:
        def invoke(self, messages):  # pragma: no cover - 机械恢复不调用 LLM
            raise AssertionError("read-before-write recovery should not call LLM replanner")

    monkeypatch.setattr("app.agent.graphs.memory_chat.nodes.get_planner_chat_model", lambda: FailingPlannerModel())
    task = {
        "id": "task-write-run",
        "goal": "修改文件并运行",
        "status": "RUNNING",
        "current_step_id": "write_code",
        "steps": [
            {
                "id": "write_code",
                "kind": "tool",
                "tool_name": "write_file",
                "arguments": {"path": "E:/demo/main.py", "content": "print(1)", "overwrite": True},
                "dependencies": [],
                "status": "EXECUTING",
            },
            {
                "id": "run_code",
                "kind": "tool",
                "tool_name": "exec_command",
                "arguments": {"command": "python main.py", "cwd": "E:/demo"},
                "dependencies": ["write_code"],
                "status": "PENDING",
            },
        ],
        "world_state": {"observations": [], "failures": [], "read_files": {}, "written_files": {}},
        "execution_history": [],
        "replan_count": 0,
    }
    failed_task = _update_task_after_tool_observation(
        {"task": task, "pending_tool_action": {"source_step_id": "write_code"}},
        {
            "tool_name": "write_file",
            "arguments": {"path": "E:/demo/main.py", "overwrite": True},
            "ok": False,
            "data": {},
            "error_code": "READ_BEFORE_WRITE_REQUIRED",
            "message": "覆盖前必须先读。",
            "blocked": True,
        },
    )
    status = _evaluate_world_status(failed_task, failed_task["world_state"], {"user_message": "修改文件并运行"})
    replanned = _replan_task_from_world_status({"user_message": "修改文件并运行"}, failed_task, status)

    assert replanned is not None
    assert [step["id"] for step in replanned["pending_steps"]] == [
        "read_before_write_code",
        "retry_write_code",
        "run_code",
    ]


def test_invalid_plan_dependency_replan_replaces_pending_steps(monkeypatch):
    """非法依赖属于计划结构错误，重规划应替换旧 pending，而不是重复拼接。"""

    class ReplacementReplanner:
        def invoke(self, messages):
            class Response:
                content = """
                {
                  "needs_task": true,
                  "reason": "原计划依赖不存在，替换为可执行计划。",
                  "steps": [
                    {
                      "id": "write_script",
                      "description": "写入脚本",
                      "kind": "tool",
                      "tool_name": "write_file",
                      "arguments": {"path": "E:/demo/main.py", "content": "print(1)", "overwrite": true},
                      "dependencies": []
                    },
                    {
                      "id": "run_script",
                      "description": "运行脚本",
                      "kind": "tool",
                      "tool_name": "exec_command",
                      "arguments": {"command": "python main.py", "cwd": "E:/demo"},
                      "dependencies": ["write_script"]
                    }
                  ]
                }
                """

            return Response()

    monkeypatch.setattr("app.agent.graphs.memory_chat.nodes.get_planner_chat_model", lambda: ReplacementReplanner())
    task = {
        "id": "task-invalid",
        "goal": "写入并运行脚本",
        "status": "READY",
        "pending_steps": [
            {
                "id": "edit_main",
                "kind": "tool",
                "tool_name": "read_file",
                "arguments": {"path": "E:/demo/src/main.py"},
                "dependencies": ["missing_step"],
                "status": "PENDING",
            }
        ],
        "completed_steps": [],
        "failed_steps": [],
        "steps": [],
        "world_state": {"observations": [], "failures": [], "read_files": {}, "written_files": {}},
        "execution_history": [],
        "replan_count": 0,
    }
    status = _evaluate_world_status(task, task["world_state"], {"user_message": "写入并运行脚本"})
    replanned = _replan_task_from_world_status({"user_message": "写入并运行脚本"}, task, status)

    assert status["last_error"]["error_code"] == "INVALID_PLAN_DEPENDENCY"
    assert replanned is not None
    assert [step["id"] for step in replanned["pending_steps"]] == ["write_script", "run_script"]


def test_task_step_history_dedupes_attempts_for_same_step_id():
    """同一 step.id 多次失败时应更新 attempt/last_error，而不是重复堆历史。"""

    base_task = {
        "id": "task-retry",
        "goal": "运行脚本",
        "status": "READY",
        "pending_steps": [
            {
                "id": "run_script",
                "kind": "tool",
                "tool_name": "exec_command",
                "arguments": {"command": "python main.py", "cwd": "E:/demo"},
                "dependencies": [],
                "status": "PENDING",
            }
        ],
        "completed_steps": [],
        "failed_steps": [],
        "steps": [],
        "world_state": {"observations": [], "failures": [], "read_files": {}, "written_files": {}},
        "execution_history": [],
        "replan_count": 0,
    }
    first = _update_task_after_tool_observation(
        {"task": base_task, "pending_tool_action": {"source_step_id": "run_script"}},
        {
            "tool_name": "exec_command",
            "arguments": {"command": "python main.py", "cwd": "E:/demo"},
            "ok": False,
            "data": {"stderr": "first failure"},
            "error_code": "COMMAND_EXITED_NON_ZERO",
            "message": "第一次失败",
            "blocked": False,
        },
    )
    retry_task = {
        **first,
        "status": "READY",
        "pending_steps": [
            {
                "id": "run_script",
                "kind": "tool",
                "tool_name": "exec_command",
                "arguments": {"command": "python main.py", "cwd": "E:/demo"},
                "dependencies": [],
                "status": "PENDING",
            }
        ],
    }
    second = _update_task_after_tool_observation(
        {"task": retry_task, "pending_tool_action": {"source_step_id": "run_script"}},
        {
            "tool_name": "exec_command",
            "arguments": {"command": "python main.py", "cwd": "E:/demo"},
            "ok": False,
            "data": {"stderr": "second failure"},
            "error_code": "COMMAND_EXITED_NON_ZERO",
            "message": "第二次失败",
            "blocked": False,
        },
    )

    assert len(second["failed_steps"]) == 1
    assert second["failed_steps"][0]["id"] == "run_script"
    assert second["failed_steps"][0]["attempt_count"] >= 2
    assert second["failed_steps"][0]["last_error"]["stderr_excerpt"] == "second failure"


def test_replanner_prompt_forbids_repeating_same_failed_tool_call(monkeypatch):
    """replanner prompt 应有通用约束，避免无变化重复失败命令。"""

    captured = {}

    class CapturingModel:
        def invoke(self, messages):
            captured["prompt"] = messages[0].content

            class Response:
                content = '{"needs_task": false}'

            return Response()

    monkeypatch.setattr("app.agent.graphs.memory_chat.nodes.get_planner_chat_model", lambda: CapturingModel())
    task = {
        "id": "task-replan",
        "goal": "运行脚本",
        "status": "REPLANNING",
        "pending_steps": [],
        "completed_steps": [],
        "failed_steps": [],
        "steps": [],
        "world_state": {"observations": [], "failures": [], "read_files": {}, "written_files": {}},
        "execution_history": [],
        "replan_count": 0,
    }
    _llm_replan_dynamic_task(
        {"user_message": "运行脚本"},
        task,
        {
            "requires_replan": True,
            "last_error": {"tool_name": "exec_command", "error_code": "COMMAND_EXITED_NON_ZERO"},
            "missing_requirements": ["需要成功执行命令并获得运行结果。"],
        },
    )

    assert "禁止无修改地重复执行完全相同的失败工具调用" in captured["prompt"]
    assert "必须先分析最近失败 observation" in captured["prompt"]
    assert "recent_failures" in captured["prompt"]


def test_exec_failure_details_are_promoted_to_world_status_and_context():
    """exec 失败详情必须进入 failure/world_status/tool_context，而不是只保留错误码。"""

    observation = {
        "tool_call_id": "tool-1-exec_command",
        "tool_name": "exec_command",
        "arguments": {"command": "python main.py", "cwd": "E:/demo"},
        "ok": False,
        "data": {
            "command": "python main.py",
            "cwd": "E:/demo",
            "relative_cwd": "demo",
            "exit_code": 1,
            "stdout": "",
            "stderr": "Traceback: missing module",
        },
        "error_code": "COMMAND_EXITED_NON_ZERO",
        "message": "命令以非 0 状态退出。",
        "blocked": False,
    }
    task = {
        "id": "task-error",
        "goal": "运行脚本并返回结果",
        "status": "READY",
        "pending_steps": [
            {
                "id": "run_script",
                "kind": "tool",
                "tool_name": "exec_command",
                "arguments": {"command": "python main.py", "cwd": "E:/demo"},
                "dependencies": [],
                "status": "PENDING",
            }
        ],
        "completed_steps": [],
        "failed_steps": [],
        "steps": [],
        "world_state": {"observations": [], "failures": [], "read_files": {}, "written_files": {}},
        "execution_history": [],
        "replan_count": 0,
    }
    failed_task = _update_task_after_tool_observation(
        {"task": task, "pending_tool_action": {"source_step_id": "run_script"}},
        observation,
    )
    status = _evaluate_world_status(failed_task, failed_task["world_state"], {"user_message": "运行脚本并返回结果"})
    context = _tool_observations_to_context([observation])

    assert status["last_error"]["stderr_excerpt"] == "Traceback: missing module"
    assert status["last_error"]["command"] == "python main.py"
    assert "Traceback: missing module" in context
    assert "exit_code: 1" in context


def test_plan_patch_drop_failed_step_continues_unblocked_pending(monkeypatch):
    """失败 step 不被剩余 pending 依赖时，plan_patch 可以 drop 它并继续队列。"""

    class DropPatchModel:
        def invoke(self, messages):
            class Response:
                content = """
                {
                  "plan_patch": {
                    "action": "drop_failed_step",
                    "step_id": "create_dir",
                    "reason": "目录创建步骤已失败，但后续写文件步骤不依赖它。"
                  }
                }
                """

            return Response()

    monkeypatch.setattr("app.agent.graphs.memory_chat.nodes.get_planner_chat_model", lambda: DropPatchModel())
    task = {
        "id": "task-drop",
        "goal": "写入并运行",
        "status": "REPLANNING",
        "plan_version": 1,
        "pending_steps": [
            {
                "id": "write_file",
                "kind": "tool",
                "tool_name": "write_file",
                "arguments": {"path": "E:/demo/main.py", "content": "print(1)"},
                "dependencies": [],
                "status": "PENDING",
            }
        ],
        "completed_steps": [],
        "failed_steps": [{"id": "create_dir", "kind": "tool", "tool_name": "exec_command", "status": "FAILED"}],
        "steps": [],
        "world_state": {
            "observations": [],
            "failures": [
                {
                    "step_id": "create_dir",
                    "tool_name": "exec_command",
                    "error_code": "COMMAND_BLOCKED",
                    "message": "exec 不用于文件写入；请使用 write_file 工具。",
                }
            ],
        },
        "execution_history": [],
        "replan_count": 0,
    }
    status = _evaluate_world_status(task, task["world_state"], {"user_message": "写入并运行"})
    patched = _replan_task_from_world_status({"user_message": "写入并运行"}, task, status)

    assert patched is not None
    assert patched["status"] == "READY"
    assert patched["pending_steps"][0]["id"] == "write_file"
    assert patched["execution_history"][-1]["type"] == "plan_patched"


def test_plan_patch_drop_failed_step_refuses_when_pending_depends_on_failed(monkeypatch):
    """如果 pending 仍依赖失败 step，drop_failed_step 不能绕过依赖。"""

    class DropPatchModel:
        def invoke(self, messages):
            class Response:
                content = '{"plan_patch":{"action":"drop_failed_step","step_id":"write_file","reason":"try drop"}}'

            return Response()

    monkeypatch.setattr("app.agent.graphs.memory_chat.nodes.get_planner_chat_model", lambda: DropPatchModel())
    task = {
        "id": "task-no-drop",
        "goal": "写入并运行",
        "status": "REPLANNING",
        "plan_version": 1,
        "pending_steps": [
            {
                "id": "run_file",
                "kind": "tool",
                "tool_name": "exec_command",
                "arguments": {"command": "python main.py", "cwd": "E:/demo"},
                "dependencies": ["write_file"],
                "status": "PENDING",
            }
        ],
        "completed_steps": [],
        "failed_steps": [{"id": "write_file", "kind": "tool", "tool_name": "write_file", "status": "FAILED"}],
        "steps": [],
        "world_state": {
            "observations": [],
            "failures": [{"step_id": "write_file", "tool_name": "write_file", "error_code": "WRITE_FAILED"}],
        },
        "execution_history": [],
        "replan_count": 0,
    }
    status = _evaluate_world_status(task, task["world_state"], {"user_message": "写入并运行"})
    patched = _replan_task_from_world_status({"user_message": "写入并运行"}, task, status)

    assert patched is None


def test_agent_think_with_blocked_task_does_not_fallback_to_legacy_tool_planner():
    """task 存在时，如果队列被阻塞，不能绕过 task 调旧单步 planner。"""

    def forbidden_planner(state):
        raise AssertionError("legacy planner must not run while task exists")

    state = build_agent_think_node(planner=forbidden_planner)(
        {
            "user_message": "写入并运行脚本",
            "task": {
                "id": "task-invalid",
                "goal": "写入并运行脚本",
                "status": "READY",
                "pending_steps": [
                    {
                        "id": "edit_main",
                        "kind": "tool",
                        "tool_name": "read_file",
                        "arguments": {"path": "E:/demo/src/main.py"},
                        "dependencies": ["missing_step"],
                        "status": "PENDING",
                    }
                ],
                "completed_steps": [],
                "failed_steps": [],
                "steps": [],
                "world_state": {"observations": [], "failures": [], "read_files": {}, "written_files": {}},
                "execution_history": [],
                "replan_count": 5,
            },
            "planned_tool_actions": [],
            "tool_observations": [],
            "turn_messages": [],
            "tool_budget": 20,
            "agent_loop_count": 0,
            "thought_events": [],
        }
    )

    assert state["agent_decision"]["type"] == "final_answer"
    assert "阻塞" in state["agent_decision"]["reason"]


def test_read_before_write_failure_is_resolved_after_same_path_read_and_write():
    """同路径 read/write 成功后，旧 READ_BEFORE_WRITE_REQUIRED 不再是 active failure。"""

    task = {
        "id": "task-rust",
        "goal": "写文件",
        "status": "COMPLETED",
        "steps": [{"id": "retry_write", "kind": "tool", "status": "COMPLETED", "dependencies": []}],
    }
    world_state = {
        "read_files": {"E:/demo/random_numbers.rs": {"content": "old"}},
        "written_files": {"E:/demo/random_numbers.rs": {"bytes_written": 10}},
        "observations": [],
        "failures": [
            {
                "step_id": "write_code",
                "tool_name": "write_file",
                "error_code": "READ_BEFORE_WRITE_REQUIRED",
                "path": "E:/demo/random_numbers.rs",
                "recovery_hint": "read_then_write_same_path",
            }
        ],
    }

    status = _evaluate_world_status(task, world_state, {"user_message": "写文件"})

    assert status["requires_replan"] is False
    assert status["last_error"] is None
    assert status["goal_satisfied"] is True


def test_exec_failure_survives_read_before_write_recovery_for_generic_replan(monkeypatch):
    """机械恢复完成后，旧 exec 失败仍应交给通用 replanner，而不是被静默吞掉。"""

    class GenericReplanner:
        def invoke(self, messages):
            class Response:
                content = """
                {
                  "needs_task": true,
                  "reason": "根据 exec stderr 修复程序后重新运行。",
                  "steps": [
                    {
                      "id": "inspect_generated_file",
                      "description": "读取已生成文件，结合执行错误分析修复方式",
                      "kind": "tool",
                      "tool_name": "read_file",
                      "arguments": {"path": "E:/demo/random_numbers.rs"},
                      "dependencies": []
                    },
                    {
                      "id": "rerun_after_fix",
                      "description": "重新运行修复后的程序",
                      "kind": "tool",
                      "tool_name": "exec_command",
                      "arguments": {"command": "rustc random_numbers.rs -o random_numbers.exe && .\\\\random_numbers.exe", "cwd": "E:/demo"},
                      "dependencies": ["inspect_generated_file"]
                    }
                  ]
                }
                """

            return Response()

    monkeypatch.setattr("app.agent.graphs.memory_chat.nodes.get_planner_chat_model", lambda: GenericReplanner())

    world_state = {
        "written_files": {"E:/demo/random_numbers.rs": {"bytes_written": 100}},
        "read_files": {"E:/demo/random_numbers.rs": {"content": "old"}},
        "generated_outputs": {},
        "approvals": [],
        "observations": [
            {
                "tool_name": "write_file",
                "ok": True,
                "data": {"path": "E:/demo/random_numbers.rs"},
            },
            {
                "tool_name": "exec_command",
                "ok": False,
                "data": {
                    "stderr": "compiler error: generated code cannot be built"
                },
                "error_code": "COMMAND_EXITED_NON_ZERO",
                "message": "命令以非 0 状态退出。",
            },
            {
                "tool_name": "read_file",
                "ok": True,
                "data": {"path": "E:/demo/random_numbers.rs", "content": "old"},
            },
            {
                "tool_name": "write_file",
                "ok": True,
                "data": {"path": "E:/demo/random_numbers.rs", "bytes_written": 120},
            },
        ],
        "failures": [
            {
                "step_id": "run_rust",
                "tool_name": "exec_command",
                "error_code": "COMMAND_EXITED_NON_ZERO",
                "message": "命令以非 0 状态退出。",
            },
            {
                "step_id": "retry_write",
                "tool_name": "write_file",
                "error_code": "READ_BEFORE_WRITE_REQUIRED",
                "path": "E:/demo/random_numbers.rs",
                "recovery_hint": "read_then_write_same_path",
            },
        ],
    }
    task = {
        "id": "task-rust",
        "goal": "生成 8 个随机数并运行",
        "status": "COMPLETED",
        "plan_version": 2,
        "current_step_id": None,
        "world_state": world_state,
        "steps": [{"id": "retry_write", "kind": "tool", "tool_name": "write_file", "status": "COMPLETED"}],
        "execution_history": [],
        "replan_count": 0,
    }

    world_status = _evaluate_world_status(task, world_state, {"user_message": "生成8个随机数并运行"})
    replanned = _replan_task_from_world_status({"user_message": "生成8个随机数并运行"}, task, world_status)

    assert world_status["requires_replan"] is True
    assert world_status["last_error"]["tool_name"] == "exec_command"
    assert replanned is not None
    assert replanned["pending_steps"][0]["tool_name"] == "read_file"
    assert replanned["pending_steps"][1]["tool_name"] == "exec_command"


def test_agent_think_does_not_fallback_to_single_tool_planner_when_task_needs_replan(monkeypatch):
    """REPLANNING task 不能绕过 replan 继续让单步 planner 反复生成 exec。"""

    class EmptyReplanner:
        def invoke(self, messages):
            class Response:
                content = '{"needs_task": false}'

            return Response()

    monkeypatch.setattr("app.agent.graphs.memory_chat.nodes.get_planner_chat_model", lambda: EmptyReplanner())

    state = build_agent_think_node()(
        {
            "user_message": "运行 Rust 程序并返回结果",
            "task": {
                "id": "task-rust",
                "goal": "运行 Rust 程序并返回结果",
                "status": "REPLANNING",
                "plan_version": 1,
                "current_step_id": None,
                "world_state": {
                    "observations": [
                        {
                            "tool_name": "exec_command",
                            "ok": False,
                            "data": {"stderr": "no targets specified in the manifest"},
                            "error_code": "COMMAND_EXITED_NON_ZERO",
                            "message": "命令以非 0 状态退出。",
                        }
                    ],
                    "failures": [
                        {
                            "step_id": "run_rust_program",
                            "tool_name": "exec_command",
                            "error_code": "COMMAND_EXITED_NON_ZERO",
                            "message": "命令以非 0 状态退出。",
                        }
                    ],
                    "read_files": {},
                    "written_files": {},
                    "known_files": {},
                    "generated_outputs": {},
                    "approvals": [],
                },
                "steps": [
                    {
                        "id": "run_rust_program",
                        "kind": "tool",
                        "tool_name": "exec_command",
                        "arguments": {"command": "cargo run", "cwd": "E:/demo"},
                        "dependencies": [],
                        "status": "FAILED",
                    }
                ],
                "execution_history": [],
                "replan_count": 0,
            },
            "world_status": {
                "goal_satisfied": False,
                "missing_requirements": ["需要成功执行命令并获得运行结果。"],
                "requires_replan": True,
                "replan_reason": "run_rust_program 执行失败，需要重规划。",
                "last_error": {"error_code": "COMMAND_EXITED_NON_ZERO"},
                "completed_steps": [],
                "failed_steps": ["run_rust_program"],
                "next_step_id": None,
            },
            "planned_tool_actions": [],
            "tool_observations": [
                {
                    "tool_name": "exec_command",
                    "ok": False,
                    "error_code": "COMMAND_EXITED_NON_ZERO",
                    "data": {"stderr": "no targets specified in the manifest"},
                }
            ],
            "turn_messages": [],
            "tool_budget": 20,
            "agent_loop_count": 2,
            "thought_events": [],
        }
    )

    assert state["agent_decision"]["type"] == "final_answer"
    assert "没有可执行" in state["agent_decision"]["reason"]
    assert not state.get("planned_tool_actions")


def test_missing_exec_fallback_does_not_repeat_same_failed_command(monkeypatch):
    """同一 exec 失败后没有新前置条件时，兜底运行步骤不能反复入队。"""

    class EmptyReplanner:
        def invoke(self, messages):
            class Response:
                content = '{"needs_task": false}'

            return Response()

    monkeypatch.setattr("app.agent.graphs.memory_chat.nodes.get_planner_chat_model", lambda: EmptyReplanner())
    task = {
        "id": "task-repeat-exec",
        "goal": "生成脚本并运行结果",
        "status": "REPLANNING",
        "plan_version": 2,
        "pending_steps": [],
        "completed_steps": [
            {
                "id": "write_script",
                "kind": "tool",
                "tool_name": "write_file",
                "arguments": {"path": "E:/demo/main.py", "content": "print(1)"},
                "status": "COMPLETED",
            }
        ],
        "failed_steps": [
            {
                "id": "run_generated_file",
                "kind": "tool",
                "tool_name": "exec_command",
                "arguments": {"command": "python main.py", "cwd": "E:/demo"},
                "status": "FAILED",
            }
        ],
        "steps": [],
        "world_state": {
            "written_files": {"E:/demo/main.py": {"bytes_written": 8}},
            "read_files": {},
            "generated_outputs": {},
            "approvals": [],
            "observations": [
                {"tool_name": "write_file", "ok": True, "data": {"path": "E:/demo/main.py"}},
                {
                    "tool_name": "exec_command",
                    "ok": False,
                    "arguments": {"command": "python main.py", "cwd": "E:/demo"},
                    "data": {"command": "python main.py", "cwd": "E:/demo", "exit_code": 1, "stderr": "boom"},
                    "error_code": "COMMAND_EXITED_NON_ZERO",
                    "message": "命令以非 0 状态退出。",
                },
            ],
            "failures": [
                {
                    "step_id": "run_generated_file",
                    "tool_name": "exec_command",
                    "error_code": "COMMAND_EXITED_NON_ZERO",
                    "message": "命令以非 0 状态退出。",
                    "command": "python main.py",
                    "cwd": "E:/demo",
                    "stderr_excerpt": "boom",
                }
            ],
        },
        "execution_history": [],
        "replan_count": 0,
    }
    world_status = _evaluate_world_status(task, task["world_state"], {"user_message": "运行并返回结果"})

    replanned = _replan_task_from_world_status({"user_message": "运行并返回结果"}, task, world_status)

    assert replanned is None


def test_missing_exec_fallback_allows_retry_after_precondition_change(monkeypatch):
    """失败后如果文件或认知状态变化，兜底运行步骤可以再次入队。"""

    class EmptyReplanner:
        def invoke(self, messages):
            class Response:
                content = '{"needs_task": false}'

            return Response()

    monkeypatch.setattr("app.agent.graphs.memory_chat.nodes.get_planner_chat_model", lambda: EmptyReplanner())
    task = {
        "id": "task-retry-after-change",
        "goal": "生成脚本并运行结果",
        "status": "REPLANNING",
        "plan_version": 2,
        "pending_steps": [],
        "completed_steps": [],
        "failed_steps": [],
        "steps": [],
        "world_state": {
            "written_files": {"E:/demo/main.py": {"bytes_written": 9}},
            "read_files": {},
            "generated_outputs": {},
            "approvals": [],
            "observations": [
                {
                    "tool_name": "exec_command",
                    "ok": False,
                    "arguments": {"command": "python main.py", "cwd": "E:/demo"},
                    "data": {"command": "python main.py", "cwd": "E:/demo", "exit_code": 1, "stderr": "boom"},
                    "error_code": "COMMAND_EXITED_NON_ZERO",
                    "message": "命令以非 0 状态退出。",
                },
                {"tool_name": "write_file", "ok": True, "data": {"path": "E:/demo/main.py"}},
            ],
            "failures": [
                {
                    "step_id": "run_generated_file",
                    "tool_name": "exec_command",
                    "error_code": "COMMAND_EXITED_NON_ZERO",
                    "message": "命令以非 0 状态退出。",
                    "command": "python main.py",
                    "cwd": "E:/demo",
                    "stderr_excerpt": "boom",
                }
            ],
        },
        "execution_history": [],
        "replan_count": 0,
    }
    world_status = {
        "goal_satisfied": False,
        "missing_requirements": ["需要成功执行命令并获得运行结果。"],
        "requires_replan": True,
        "replan_reason": "缺少运行结果。",
        "last_error": None,
        "completed_steps": [],
        "failed_steps": [],
        "next_step_id": None,
    }

    replanned = _replan_task_from_world_status({"user_message": "运行并返回结果"}, task, world_status)

    assert replanned is not None
    assert replanned["pending_steps"][0]["tool_name"] == "exec_command"


def test_llm_replan_debug_records_rejected_raw_response(monkeypatch):
    """replanner 无有效 steps 时，应保留原始输出和拒绝原因供 graph 调试。"""

    class EmptyReplanner:
        def invoke(self, messages):
            class Response:
                content = '{"needs_task": false, "reason": "暂时无法规划"}'

            return Response()

    monkeypatch.setattr("app.agent.graphs.memory_chat.nodes.get_planner_chat_model", lambda: EmptyReplanner())
    task = {
        "id": "task-debug",
        "goal": "运行脚本",
        "status": "REPLANNING",
        "pending_steps": [],
        "completed_steps": [],
        "failed_steps": [],
        "steps": [],
        "world_state": {"observations": [], "failures": [], "read_files": {}, "written_files": {}},
        "execution_history": [],
        "replan_count": 0,
    }

    replanned = _llm_replan_dynamic_task(
        {"user_message": "运行脚本"},
        task,
        {
            "requires_replan": True,
            "replan_reason": "缺少运行结果。",
            "last_error": None,
            "missing_requirements": ["需要成功执行命令并获得运行结果。"],
        },
    )

    assert replanned is None
    debug_items = task["world_state"]["replan_debug"]
    assert debug_items[-1]["status"] == "rejected_no_steps"
    assert "needs_task" in debug_items[-1]["raw_response"]
    assert debug_items[-1]["parsed_payload"]["reason"] == "暂时无法规划"


def test_llm_replan_debug_records_accepted_steps(monkeypatch):
    """replanner 接受新 steps 时，也应记录 normalized step 信息。"""

    class StepReplanner:
        def invoke(self, messages):
            class Response:
                content = """
                {
                  "needs_task": true,
                  "reason": "补充读取后运行。",
                  "steps": [
                    {
                      "id": "inspect_file",
                      "description": "读取文件",
                      "kind": "tool",
                      "tool_name": "read_file",
                      "arguments": {"path": "E:/demo/main.py"},
                      "dependencies": []
                    }
                  ]
                }
                """

            return Response()

    monkeypatch.setattr("app.agent.graphs.memory_chat.nodes.get_planner_chat_model", lambda: StepReplanner())
    task = {
        "id": "task-debug-accepted",
        "goal": "运行脚本",
        "status": "REPLANNING",
        "pending_steps": [],
        "completed_steps": [],
        "failed_steps": [],
        "steps": [],
        "world_state": {"observations": [], "failures": [], "read_files": {}, "written_files": {}},
        "execution_history": [],
        "replan_count": 0,
    }

    replanned = _llm_replan_dynamic_task(
        {"user_message": "运行脚本"},
        task,
        {
            "requires_replan": True,
            "replan_reason": "缺少运行结果。",
            "last_error": None,
            "missing_requirements": ["需要成功执行命令并获得运行结果。"],
        },
    )

    assert replanned is not None
    debug_items = replanned["world_state"]["replan_debug"]
    assert debug_items[-1]["status"] == "accepted_steps"
    assert debug_items[-1]["normalized_step_ids"] == ["inspect_file"]


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


def test_plan_task_treats_local_operation_confirmation_as_continuation(monkeypatch):
    """“可以，直接覆盖吧”应结合上一轮 assistant 建议进入工具规划。"""

    class FollowupPlanner:
        def invoke(self, messages):
            prompt = messages[0].content
            assert "followup_hint" in prompt
            assert "Cargo.toml" in prompt

            class Response:
                content = """
                {
                  "needs_task": true,
                  "goal": "覆盖 Cargo.toml 并运行 cargo run",
                  "reason": "用户确认继续上一轮本地操作。",
                  "steps": [
                    {
                      "id": "read_manifest",
                      "kind": "tool",
                      "description": "读取 manifest",
                      "tool_name": "read_file",
                      "arguments": {"path": "E:/demo/Cargo.toml"},
                      "dependencies": []
                    },
                    {
                      "id": "write_manifest",
                      "kind": "tool",
                      "description": "覆盖 manifest",
                      "tool_name": "write_file",
                      "arguments": {"path": "E:/demo/Cargo.toml", "content": "[package]\\nname=\\"demo\\"\\n", "overwrite": true},
                      "dependencies": ["read_manifest"]
                    },
                    {
                      "id": "run_project",
                      "kind": "tool",
                      "description": "运行项目",
                      "tool_name": "exec_command",
                      "arguments": {"command": "cargo run", "cwd": "E:/demo"},
                      "dependencies": ["write_manifest"]
                    }
                  ]
                }
                """

            return Response()

    monkeypatch.setattr("app.agent.graphs.memory_chat.nodes.get_planner_chat_model", lambda: FollowupPlanner())
    state = build_plan_task_node()(
        {
            "user_message": "可以，你直接覆盖吧",
            "recent_messages": [
                {
                    "id": 1,
                    "role": "assistant",
                    "content": "我需要把 E:/demo/Cargo.toml 重写为标准二进制项目格式，然后重新运行 cargo run。你希望我直接覆盖写入这个文件吗？",
                    "token_count": 0,
                }
            ],
            "thought_events": [],
        }
    )

    assert state["task"]["pending_steps"][0]["tool_name"] == "read_file"
    assert state["task"]["pending_steps"][1]["tool_name"] == "write_file"
    assert state["task"]["pending_steps"][2]["tool_name"] == "exec_command"


def test_generate_answer_blocks_unobserved_local_operation_followup():
    """确认继续本地操作但本轮无 observation 时，回答层不能编造完成结果。"""

    def forbidden_generator(*args, **kwargs):
        raise AssertionError("answer generator must not be called for unobserved local operation")

    state = build_generate_answer_node(answer_generator=forbidden_generator)(
        {
            "user_message": "可以，你直接覆盖吧",
            "recent_messages": [
                {
                    "id": 1,
                    "role": "assistant",
                    "content": "我需要把 E:/demo/Cargo.toml 重写为标准二进制项目格式，并重新运行 cargo run。你希望我直接覆盖写入这个文件吗？",
                    "token_count": 0,
                }
            ],
            "retrieved_chunks": [],
            "needs_retrieval": False,
            "retrieval_grade": "none",
            "tool_observations": [],
            "turn_messages": [],
        }
    )

    assert "还没有实际执行" in state["assistant_answer"]
    assert "不能声称已经覆盖文件" in state["assistant_answer"]


def test_agent_tool_planner_prompt_does_not_stop_after_write_when_run_requested():
    """写入 observation 不能单独终止任务；运行结果类目标必须继续允许 exec。"""

    prompt = _build_agent_tool_planner_prompt(
        {
            "user_message": "在 E:\\demo 创建一个 rust程序，生成8个随机数，写好后将运行结果给我",
            "prompt_context": "",
            "turn_messages": [
                {
                    "role": "user",
                    "name": "current_user_input",
                    "content": "在 E:\\demo 创建一个 rust程序，生成8个随机数，写好后将运行结果给我",
                }
            ],
            "tool_observations": [
                {
                    "tool_call_id": "tool-1-write_file",
                    "tool_name": "write_file",
                    "arguments": {"path": "E:/demo/random_numbers.rs"},
                    "ok": True,
                    "data": {"path": "E:/demo/random_numbers.rs", "bytes_written": 128},
                    "error_code": "",
                    "message": "",
                    "blocked": False,
                }
            ],
        }
    )

    assert "工具已经成功写入后，不要继续调用工具" not in prompt
    assert "write_file 成功后，如果当前目标还要求运行、编译、测试、验证或返回运行结果，必须继续选择 exec_command" in prompt
    assert "不能单独决定整个任务终止" in prompt


def test_world_status_requires_exec_result_for_run_goal_after_write_only():
    """用户要求运行结果时，仅有 write_file observation 不代表目标完成。"""

    task = {
        "id": "task-rust",
        "goal": "创建 Rust 程序并将运行结果给我",
        "status": "COMPLETED",
        "steps": [
            {
                "id": "write_rust",
                "kind": "tool",
                "tool_name": "write_file",
                "status": "COMPLETED",
                "dependencies": [],
            }
        ],
    }
    world_state = {
        "observations": [
            {
                "tool_name": "write_file",
                "ok": True,
                "data": {"path": "E:/demo/random_numbers.rs"},
            }
        ],
        "failures": [],
    }

    status = _evaluate_world_status(
        task,
        world_state,
        {"user_message": "在 E:\\demo 创建一个 rust程序，生成8个随机数，写好后将运行结果给我"},
    )

    assert status["goal_satisfied"] is False
    assert status["requires_replan"] is True
    assert status["replan_reason"] == "任务目标仍缺少必要结果，需要补充后续步骤。"


def test_verify_goal_rejects_exec_output_that_does_not_match_random_number_goal():
    """exec 成功不等于目标完成；输出不含 8 个随机数时必须回到 replan。"""

    state = build_verify_goal_node()(
        {
            "user_message": "在 E:\\demo 创建一个 rust程序，生成8个随机数，写好后将运行结果给我",
            "task": {
                "id": "task-random",
                "goal": "创建 Rust 程序，生成 8 个随机数并返回运行结果",
                "status": "COMPLETED",
                "pending_steps": [],
                "completed_steps": [{"id": "run_program", "kind": "tool", "tool_name": "exec_command"}],
                "failed_steps": [],
                "steps": [],
                "world_state": {
                    "observations": [
                        {
                            "tool_call_id": "tool-1-exec_command",
                            "tool_name": "exec_command",
                            "ok": True,
                            "data": {
                                "command": "cargo run",
                                "cwd": "E:/demo",
                                "exit_code": 0,
                                "stdout": "Hello, updated world!\n",
                                "stderr": "",
                            },
                        }
                    ],
                    "failures": [],
                },
            },
            "thought_events": [],
        }
    )

    assert state["goal_verification"]["satisfied"] is False
    assert "8 个随机数" in state["goal_verification"]["contradictions"][0]
    assert state["world_status"]["requires_replan"] is True
    assert state["agent_decision"]["type"] == "replan"


def test_verify_goal_accepts_exec_output_that_matches_random_number_goal():
    """当真实 stdout 满足目标时，verify_goal 才允许最终回答。"""

    state = build_verify_goal_node()(
        {
            "user_message": "生成8个随机数并给我运行结果",
            "task": {
                "id": "task-random",
                "goal": "生成 8 个随机数并返回运行结果",
                "status": "COMPLETED",
                "pending_steps": [],
                "completed_steps": [{"id": "run_program", "kind": "tool", "tool_name": "exec_command"}],
                "failed_steps": [],
                "steps": [],
                "world_state": {
                    "observations": [
                        {
                            "tool_call_id": "tool-1-exec_command",
                            "tool_name": "exec_command",
                            "ok": True,
                            "data": {
                                "command": "python main.py",
                                "cwd": "E:/demo",
                                "exit_code": 0,
                                "stdout": "12 44 8 91 3 70 25 61\n",
                                "stderr": "",
                            },
                        }
                    ],
                    "failures": [],
                },
            },
            "thought_events": [],
        }
    )

    assert state["goal_verification"]["satisfied"] is True
    assert state["world_status"]["goal_satisfied"] is True
    assert state["agent_decision"]["type"] == "final_answer"


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
