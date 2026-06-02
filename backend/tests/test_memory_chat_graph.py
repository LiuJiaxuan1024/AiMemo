from pathlib import Path

from langchain_core.messages import AIMessage
from langchain_core.messages import ToolMessage
from langgraph.types import Interrupt
from sqlmodel import select

from app.agent.graphs.memory_chat.graph import build_memory_chat_graph, run_memory_chat_graph
from app.agent.graphs.memory_chat.graph import _resolve_graph_input_for_turn
from app.agent.checkpoints import get_sqlite_checkpointer
from app.agent.graphs.memory_chat.nodes import RetrievalPlan
from app.agent.graphs.memory_chat.nodes import build_memory_chat_answer_system_prompt
from app.agent.graphs.memory_chat.nodes import build_agent_node
from app.agent.graphs.memory_chat.nodes import build_tools_node
from app.agent.graphs.memory_chat.nodes import route_after_agent
from app.agent.graphs.memory_chat.nodes import _build_react_agent_system_prompt
from app.agent.graphs.memory_chat.nodes import _build_react_agent_messages
from app.agent.graphs.memory_chat.nodes import _extract_ai_tool_calls
from app.agent.graphs.memory_chat.nodes import _run_agent_tool_action
from app.agent.graphs.memory_chat.nodes import _normalize_user_input_resume
from app.agent.graphs.memory_chat.nodes import build_load_turn_state_node
from app.agent.graphs.memory_chat.nodes import default_retrieval_planner
from app.agent.graphs.memory_chat.nodes import _should_retrieve_mounted_knowledge
from app.agent.graphs.memory_chat.nodes import _build_model_messages
from app.agent.graphs.memory_chat.nodes import _parse_elf_bubble_parts
from app.agent.graphs.memory_chat.nodes import build_merge_prompt_context_node
from app.agent.graphs.memory_chat.nodes import build_l3_knowledge_context_node
from app.agent.graphs.memory_chat.nodes import _tool_observations_to_context
from app.agent.graphs.memory_chat.nodes import build_observe_tool_result_node
from app.agent.graphs.memory_chat.nodes import build_plan_task_node
from app.agent.graphs.memory_chat.nodes import build_verify_goal_node
from app.models.chat_message import ChatMessage
from app.models.knowledge import ConversationKnowledgeMount, KnowledgeChunk, KnowledgeDocument, KnowledgeSpace
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


def test_chat_turn_state_history_serializes_langgraph_interrupts(
    session,
    tmp_path: Path,
):
    """Checkpoint history 中的 LangGraph Interrupt 应结构化返回，不能触发 500。"""

    conversation = create_conversation(session, ConversationCreate(title="interrupt history"))
    checkpoint_path = tmp_path / "interrupts.db"

    from app.models.chat_turn import ChatTurn
    from app.services.chat_turn_service import _to_checkpoint_state_read

    with get_sqlite_checkpointer(str(checkpoint_path)) as checkpointer:
        app = build_memory_chat_graph(session_factory=lambda: session).compile(checkpointer=checkpointer)
        config = {"configurable": {"thread_id": f"conversation:{conversation.id}"}}
        app.update_state(
            config,
            {"user_message": "需要选择目录"},
            as_node="agent",
        )
        snapshot = app.get_state(config)

    snapshot = snapshot._replace(
        interrupts=(
            Interrupt(
                value={
                    "kind": "user_input",
                    "request_id": "request-1",
                    "question": "文件写到哪里？",
                    "options": [{"id": "a", "label": "E:/demo"}],
                },
                id="interrupt-1",
            ),
        )
    )
    state = _to_checkpoint_state_read(snapshot)
    payload = state.model_dump(mode="json")

    assert payload["interrupts"][0]["type"] == "Interrupt"
    assert payload["interrupts"][0]["id"] == "interrupt-1"
    assert payload["interrupts"][0]["value"]["request_id"] == "request-1"


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
            "agent_decision": {"type": "tool_call"},
            },
            as_node="agent",
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
            parent_message_id=None,
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
                "agent_decision": {"type": "tool_call"},
                "task": {"id": "task-old", "status": "RUNNING", "execution_history": []},
            },
            as_node="agent",
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
            parent_message_id=None,
        )
        expired = app.get_state(config)

    assert graph_input["user_message"] == "写入 B"
    assert expired.next == ()
    assert expired.values["agent_decision"]["reason"] == "旧 checkpoint 被新用户输入过期。"
    assert expired.values["tool_observations"] == []
    assert expired.values["tool_observation_context"] == ""


def test_memory_chat_graph_main_flow_is_flat_context_worker_graph(session_factory):
    graph = build_memory_chat_graph(session_factory=session_factory)
    mermaid = graph.compile().get_graph().draw_mermaid()

    assert "load_turn_state" in mermaid
    assert "dispatch_context_workers" in mermaid
    assert "build_l3_retrieved_memory" in mermaid
    assert "build_l3_knowledge_context" in mermaid
    assert "build_current_conversation_window" in mermaid
    assert "plan_task" in mermaid
    assert "agent" in mermaid
    assert "tools" in mermaid
    assert "observe_tool_result" in mermaid
    assert "verify_goal" in mermaid
    assert "agent_think" not in mermaid
    assert "select_tool" not in mermaid
    assert "check_tool_policy" not in mermaid
    assert "run_read_tool" not in mermaid
    assert "run_write_tool" not in mermaid
    assert "run_exec_tool" not in mermaid
    assert "build_local_operator_context" not in mermaid
    assert "merge_prompt_context" in mermaid
    assert "generate_elf_bubble_answer" in mermaid
    assert "plan_retrieval" not in mermaid
    assert "retrieve_notes" not in mermaid
    assert "grade_retrieval" not in mermaid


def test_l3_knowledge_context_requires_conversation_mount(session, session_factory):
    conversation = create_conversation(session, ConversationCreate(title="未挂载知库"))

    update = build_l3_knowledge_context_node(session_factory)(
        {
            "conversation_id": conversation.id,
            "user_message": "根据文档总结一下项目架构",
        }
    )

    assert update["mounted_knowledge_spaces"] == []
    assert update["needs_knowledge_retrieval"] is False
    assert update["knowledge_retrieved_chunks"] == []
    assert "未挂载知识空间" in update["context_l3_knowledge_layer"]["content"]
    assert "不能搜索或引用全局知识库" in update["context_l3_knowledge_layer"]["content"]


def test_should_retrieve_mounted_knowledge_defaults_to_search():
    mounted_spaces = [{"space_id": 1, "space_name": "C++ 迁移资料", "space_icon": "library"}]

    needs_retrieval, reason = _should_retrieve_mounted_knowledge(
        "帮我讲一下跳表在这个项目里适合怎么用",
        mounted_spaces,
    )

    assert needs_retrieval is True
    assert "默认先检索" in reason


def test_should_retrieve_mounted_knowledge_skips_only_clear_casual_or_common_fact():
    mounted_spaces = [{"space_id": 1, "space_name": "C++ 迁移资料", "space_icon": "library"}]

    casual_result = _should_retrieve_mounted_knowledge("晚上好", mounted_spaces)
    fact_result = _should_retrieve_mounted_knowledge("1+1 等于几？", mounted_spaces)
    no_mount_result = _should_retrieve_mounted_knowledge("根据资料总结一下", [])

    assert casual_result[0] is False
    assert "明确闲聊或客观常识" in casual_result[1]
    assert fact_result[0] is False
    assert "明确闲聊或客观常识" in fact_result[1]
    assert no_mount_result[0] is False
    assert "未挂载知识空间" in no_mount_result[1]


def test_l3_knowledge_context_searches_only_mounted_spaces(session, session_factory, monkeypatch):
    conversation = create_conversation(session, ConversationCreate(title="挂载知库"))
    mounted_space = KnowledgeSpace(name="C++ 迁移资料", description="mounted")
    other_space = KnowledgeSpace(name="未挂载资料", description="not mounted")
    session.add(mounted_space)
    session.add(other_space)
    session.commit()
    session.refresh(mounted_space)
    session.refresh(other_space)
    session.add(ConversationKnowledgeMount(conversation_id=conversation.id, space_id=mounted_space.id))
    mounted_doc = KnowledgeDocument(
        space_id=mounted_space.id,
        title="Zenoh 迁移方案",
        content_hash="mounted-doc",
        status="ready",
    )
    other_doc = KnowledgeDocument(
        space_id=other_space.id,
        title="不应被检索的方案",
        content_hash="other-doc",
        status="ready",
    )
    session.add(mounted_doc)
    session.add(other_doc)
    session.commit()
    session.refresh(mounted_doc)
    session.refresh(other_doc)
    mounted_chunk = KnowledgeChunk(
        space_id=mounted_space.id,
        document_id=mounted_doc.id,
        chunk_index=0,
        text="Zenoh 迁移需要先抽象传输层，再替换发现机制。",
        content_hash="chunk-mounted",
        embedding_status="completed",
    )
    other_chunk = KnowledgeChunk(
        space_id=other_space.id,
        document_id=other_doc.id,
        chunk_index=0,
        text="这段未挂载内容不能进入结果。",
        content_hash="chunk-other",
        embedding_status="completed",
    )
    session.add(mounted_chunk)
    session.add(other_chunk)
    session.commit()

    monkeypatch.setattr(
        "app.services.knowledge_search_service.search_knowledge_chunk_embeddings",
        lambda embedding, limit=12: [(mounted_chunk.id, 0.1), (other_chunk.id, 0.01)],
    )
    from app.services.knowledge_search_service import search_mounted_knowledge as real_search_mounted_knowledge

    def fake_search_mounted_knowledge(
        current_session,
        *,
        conversation_id,
        query,
        top_k=5,
        mode="hybrid",
        per_document_limit=3,
    ):
        return real_search_mounted_knowledge(
            current_session,
            conversation_id=conversation_id,
            query=query,
            top_k=top_k,
            mode=mode,
            per_document_limit=per_document_limit,
            embedding_generator=lambda texts: [[0.1, 0.2, 0.3] for _ in texts],
        )

    monkeypatch.setattr(
        "app.agent.graphs.memory_chat.nodes.search_mounted_knowledge",
        fake_search_mounted_knowledge,
    )

    update = build_l3_knowledge_context_node(session_factory)(
        {
            "conversation_id": conversation.id,
            "user_message": "根据文档总结 Zenoh 迁移方案",
        }
    )

    assert update["needs_knowledge_retrieval"] is True
    assert update["knowledge_retrieved_chunks"]
    assert update["knowledge_retrieved_chunks"][0]["space_id"] == mounted_space.id
    assert "Zenoh 迁移需要先抽象传输层" in update["context_l3_knowledge_layer"]["content"]
    assert "这段未挂载内容" not in update["context_l3_knowledge_layer"]["content"]


def test_knowledge_search_tool_respects_mount_scope(session, session_factory, monkeypatch):
    conversation = create_conversation(session, ConversationCreate(title="工具检索知库"))
    space = KnowledgeSpace(name="工具资料", description="")
    session.add(space)
    session.commit()
    session.refresh(space)
    document = KnowledgeDocument(
        space_id=space.id,
        title="工具文档",
        content_hash="tool-doc",
        status="ready",
    )
    session.add(document)
    session.commit()
    session.refresh(document)
    chunk = KnowledgeChunk(
        space_id=space.id,
        document_id=document.id,
        chunk_index=0,
        text="knowledge_search 只能搜索当前会话挂载的知识空间。",
        content_hash="tool-chunk",
        embedding_status="completed",
    )
    session.add(chunk)
    session.commit()

    no_mount_update = _run_agent_tool_action(
        {"conversation_id": conversation.id, "tool_observations": [], "turn_messages": [], "thought_events": []},
        action={
            "tool_call_id": "ks-no-mount",
            "tool_name": "knowledge_search",
            "arguments": {"query": "knowledge_search"},
        },
        session_factory=session_factory,
        allowed_tool_names={"knowledge_search"},
    )
    assert no_mount_update["tool_observations"][0]["ok"] is False
    assert no_mount_update["tool_observations"][0]["error_code"] == "NEED_KNOWLEDGE_MOUNT"

    session.add(ConversationKnowledgeMount(conversation_id=conversation.id, space_id=space.id))
    document = KnowledgeDocument(
        space_id=space.id,
        title="长文档",
        content_hash="cache-expansion-doc",
        status="ready",
    )
    session.add(document)
    session.flush()
    chunks = [
        KnowledgeChunk(
            space_id=space.id,
            document_id=document.id,
            chunk_index=index,
            text=f"缓存中的第 {index + 1} 段资料",
            content_hash=f"cache-expansion-{index}",
            embedding_status="completed",
        )
        for index in range(5)
    ]
    session.add_all(chunks)
    session.commit()
    monkeypatch.setattr(
        "app.services.knowledge_search_service.search_knowledge_chunk_embeddings",
        lambda embedding, limit=12: [(chunk.id, 0.1)],
    )
    from app.services.knowledge_search_service import search_mounted_knowledge as real_search_mounted_knowledge

    def fake_search_mounted_knowledge(
        current_session,
        *,
        conversation_id,
        query,
        top_k=5,
        mode="hybrid",
        per_document_limit=3,
    ):
        return real_search_mounted_knowledge(
            current_session,
            conversation_id=conversation_id,
            query=query,
            top_k=top_k,
            mode=mode,
            per_document_limit=per_document_limit,
            embedding_generator=lambda texts: [[0.1, 0.2, 0.3] for _ in texts],
        )

    monkeypatch.setattr(
        "app.agent.graphs.memory_chat.nodes.search_mounted_knowledge",
        fake_search_mounted_knowledge,
    )

    update = _run_agent_tool_action(
        {"conversation_id": conversation.id, "tool_observations": [], "turn_messages": [], "thought_events": []},
        action={
            "tool_call_id": "ks-mounted",
            "tool_name": "knowledge_search",
            "arguments": {"query": "knowledge_search"},
        },
        session_factory=session_factory,
        allowed_tool_names={"knowledge_search"},
    )

    observation = update["tool_observations"][0]
    assert observation["ok"] is True
    assert observation["data"]["results"][0]["space_id"] == space.id
    assert "knowledge_search 只能搜索" in update["turn_messages"][0]["content"]


def test_knowledge_search_tool_expands_from_recall_cache(session, session_factory, monkeypatch):
    conversation = create_conversation(session, ConversationCreate(title="缓存扩展知库"))
    space = KnowledgeSpace(name="缓存资料", description="")
    session.add(space)
    session.commit()
    session.refresh(space)
    session.add(ConversationKnowledgeMount(conversation_id=conversation.id, space_id=space.id))
    document = KnowledgeDocument(
        space_id=space.id,
        title="长文档",
        content_hash="cache-expansion-doc",
        status="ready",
    )
    session.add(document)
    session.flush()
    chunks = [
        KnowledgeChunk(
            space_id=space.id,
            document_id=document.id,
            chunk_index=index,
            text=f"缓存中的第 {index + 1} 段资料",
            content_hash=f"cache-expansion-{index}",
            embedding_status="completed",
        )
        for index in range(5)
    ]
    session.add_all(chunks)
    session.commit()

    def fail_search(*args, **kwargs):  # noqa: ARG001
        raise AssertionError("cache hit should not query vector search again")

    monkeypatch.setattr(
        "app.agent.graphs.memory_chat.nodes.search_mounted_knowledge",
        fail_search,
    )
    cached_chunks = [
        {
            "chunk_id": chunk.id,
            "space_id": space.id,
            "space_name": space.name,
            "document_id": document.id,
            "document_title": document.title,
            "text": chunk.text,
            "score": 1.0 - index * 0.01,
            "score_source": "hybrid",
            "heading_path": ["章节"],
            "page_number": None,
            "source_uri": None,
            "original_filename": "cache.md",
            "retrieval_phase": "hybrid_merge",
            "distance": None,
        }
        for index, chunk in enumerate(chunks)
    ]

    update = _run_agent_tool_action(
        {
            "conversation_id": conversation.id,
            "knowledge_retrieval_query": "adaptive cache",
            "knowledge_recall_cache": cached_chunks,
            "tool_observations": [],
            "turn_messages": [],
            "thought_events": [],
        },
        action={
            "tool_call_id": "ks-cache",
            "tool_name": "knowledge_search",
            "arguments": {
                "query": "adaptive cache",
                "top_k": 5,
                "retrieval_profile": "expanded",
            },
        },
        session_factory=session_factory,
        allowed_tool_names={"knowledge_search"},
    )

    observation = update["tool_observations"][0]
    assert observation["ok"] is True
    assert observation["data"]["cache_hit"] is True
    assert len(observation["data"]["results"]) == 5
    assert observation["data"]["results"][-1]["text"] == "缓存中的第 5 段资料"


def test_tools_node_preserves_knowledge_search_arguments(session_factory, monkeypatch):
    captured_actions: list[dict] = []

    def fake_run(state, *, action, session_factory, allowed_tool_names, step_index=None):  # noqa: ARG001
        captured_actions.append(action)
        obs = {
            "tool_call_id": action["tool_call_id"],
            "tool_name": action["tool_name"],
            "arguments": action.get("arguments") or {},
            "ok": True,
            "data": {"results": []},
            "error_code": "",
            "message": "ok",
            "blocked": False,
        }
        return {
            "tool_observations": [*state.get("tool_observations", []), obs],
            "turn_messages": list(state.get("turn_messages") or []),
            "thought_events": list(state.get("thought_events") or []),
        }

    monkeypatch.setattr(
        "app.agent.graphs.memory_chat.nodes._run_agent_tool_action",
        fake_run,
    )

    build_tools_node(session_factory)(
        {
            "agent_decision": {
                "type": "tool_call",
                "tool_calls": [
                    {
                        "id": "ks-args",
                        "name": "knowledge_search",
                        "args": {"query": "Zenoh 迁移", "top_k": 7, "mode": "keyword"},
                    }
                ],
            },
            "tool_observations": [],
            "turn_messages": [],
            "thought_events": [],
            "prompt_context": "",
        }
    )

    assert captured_actions[0]["arguments"] == {
        "query": "Zenoh 迁移",
        "top_k": 7,
        "mode": "keyword",
    }


def test_react_agent_routes_option_followup_to_tools(monkeypatch, session_factory):
    """“采用方案一”应由 ReAct agent 根据历史上下文选择工具，而不是规则跳过。"""

    class FakeToolBoundModel:
        def invoke(self, messages):
            rendered = "\n".join(str(getattr(message, "content", "")) for message in messages)
            assert "采用方案一" in rendered
            assert "方案一" in rendered
            assert "cargo init" in rendered
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "call-cargo-init",
                        "name": "exec_command",
                        "args": {
                            "command": "cargo init",
                            "cwd": "E:/demo",
                            "timeout_ms": 30000,
                            "max_output_bytes": 65536,
                        },
                    }
                ],
            )

    monkeypatch.setattr(
        "app.agent.graphs.memory_chat.nodes.get_agent_chat_model_with_tools",
        lambda tools: FakeToolBoundModel(),
    )

    state = build_agent_node(session_factory)(
        {
            "conversation_id": 1,
            "user_message": "采用方案一",
            "prompt_context": (
                "## L1 近期对话窗口\n"
                "assistant: 方案一（推荐）：在 E:/demo 下运行 cargo init，"
                "并在 Cargo.toml 里添加 rand 依赖。\n"
                "## L0 当前用户输入\n采用方案一"
            ),
            "turn_messages": [{"role": "user", "content": "采用方案一", "name": "current_user_input"}],
            "tool_observations": [],
        }
    )

    assert state["agent_decision"]["type"] == "tool_call"
    assert route_after_agent(state) == "tools"
    assert state["agent_decision"]["tool_calls"][0]["name"] == "exec_command"


def test_react_agent_messages_preserve_structured_tool_call_context():
    """工具循环回到 agent 时，必须保留 AIMessage.tool_calls 与 ToolMessage 对应关系。"""

    messages = _build_react_agent_messages(
        {
            "prompt_context": "## L0 当前用户输入\n列出 E:/demo",
            "turn_messages": [
                {
                    "role": "user",
                    "content": "列出 E:/demo",
                    "name": "current_user_input",
                    "tool_call_id": None,
                },
                {
                    "role": "assistant",
                    "content": "",
                    "name": "agent",
                    "tool_call_id": None,
                    "tool_calls": [
                        {
                            "id": "call-list-demo",
                            "name": "read_directory",
                            "args": {"path": "E:/demo"},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "content": '{"ok":true,"data":{"entries":["Cargo.toml"]}}',
                    "name": "read_directory",
                    "tool_call_id": "call-list-demo",
                },
            ],
        }
    )

    ai_messages = [message for message in messages if isinstance(message, AIMessage)]
    tool_messages = [message for message in messages if isinstance(message, ToolMessage)]

    assert ai_messages[-1].tool_calls[0]["id"] == "call-list-demo"
    assert ai_messages[-1].tool_calls[0]["name"] == "read_directory"
    assert tool_messages[-1].tool_call_id == "call-list-demo"


def test_task_runtime_nodes_track_world_state_and_verification():
    planned = build_plan_task_node()(
        {
            "conversation_id": 1,
            "user_message": "创建 test.cc 并运行",
            "thought_events": [],
        }
    )
    assert planned["task"]["goal"] == "创建 test.cc 并运行"
    assert "write_file" in " ".join(planned["task"]["acceptance_criteria"])

    state = {
        **planned,
        "agent_step_index": 1,
        "tool_observations": [
            {
                "tool_call_id": "call-write",
                "tool_name": "write_file",
                "arguments": {"path": "E:/demo/test.cc"},
                "ok": True,
                "data": {"path": "E:/demo/test.cc", "relative_path": "test.cc"},
                "error_code": "",
                "message": "",
                "blocked": False,
            }
        ],
    }
    observed = build_observe_tool_result_node()(state)
    assert observed["world_state"]["known_paths"]["E:/demo/test.cc"]["ok"] is True
    assert observed["task"]["steps"][0]["tool_name"] == "write_file"

    verified = build_verify_goal_node()({**state, **observed})
    assert verified["verification"]["status"] == "ready_for_agent"
    assert verified["replan_required"] is False


def test_initial_chat_turn_statuses_include_dynamic_tool_loop_nodes():
    """前端 graph 状态表应覆盖主图里的动态任务和 exec/verify 节点。"""

    statuses = initial_node_statuses()

    assert "plan_task" in statuses
    assert "agent" in statuses
    assert "tools" in statuses
    assert "observe_tool_result" in statuses
    assert "verify_goal" in statuses


def test_react_agent_prompt_includes_absolute_path_and_tool_discipline():
    prompt = _build_react_agent_system_prompt()

    assert "path/root/cwd 参数都必须传绝对路径" in prompt
    assert "E:\\demo" in prompt
    assert "不能替代读取正文" in prompt
    assert "不要原样盲目重试" in prompt
    assert "多个互不依赖" in prompt



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


def test_memory_chat_graph_degrades_when_l3_retriever_fails(
    session,
    session_factory,
    tmp_path: Path,
):
    """L3 检索链路失败时不能中断主对话。"""

    conversation = create_conversation(session, ConversationCreate(title="检索降级"))

    def fake_planner(user_message, recent_messages):
        return RetrievalPlan(
            intent="rag",
            needs_retrieval=True,
            needs_query_rewrite=False,
            retrieval_query=user_message,
            confidence=0.9,
            reason="测试强制检索。",
            source="test",
        )

    def broken_retriever(current_session, *, query: str, limit: int):
        raise OSError(233, "管道的另一端上无任何进程。")

    def fake_answer(user_message, recent_messages, retrieved_chunks, needs_retrieval, retrieval_grade):
        assert retrieved_chunks == []
        assert needs_retrieval is False
        assert retrieval_grade == "none"
        return "检索暂时不可用，但我还能继续回答。"

    result = run_memory_chat_graph(
        conversation_id=conversation.id,
        user_message="帮我打开 bilibili",
        session_factory=session_factory,
        checkpoint_path=str(tmp_path / "checkpoints.db"),
        planner=fake_planner,
        retriever=broken_retriever,
        answer_generator=fake_answer,
    )

    assert result["needs_retrieval"] is False
    assert result["retrieval_grade"] == "none"
    assert result["retrieval_debug"]["degraded"] is True
    assert result["retrieval_debug"]["failed_stage"] == "retriever"
    assert "L3 检索失败" in result["retrieval_grade_reason"]


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
        interrupt_after=["agent"],
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


def test_agent_node_circuit_breaks_after_consecutive_tool_failures(session_factory, monkeypatch):
    """连续工具失败累计到阈值时，agent 节点必须跳过 LLM 直接产兜底回答。"""

    def boom(*_args, **_kwargs):
        raise AssertionError("熔断时不应再调用 LLM")

    monkeypatch.setattr(
        "app.agent.graphs.memory_chat.nodes.get_agent_chat_model_with_tools",
        boom,
    )

    state = build_agent_node(session_factory)(
        {
            "conversation_id": 1,
            "user_message": "再试一次",
            "prompt_context": "## L0 当前用户输入\n再试一次",
            "turn_messages": [{"role": "user", "content": "再试一次", "name": "current_user_input"}],
            "tool_observations": [
                {"tool_name": "exec_command", "ok": False, "error_code": "X", "message": "失败 A"},
                {"tool_name": "exec_command", "ok": False, "error_code": "Y", "message": "失败 B"},
                {"tool_name": "exec_command", "ok": False, "error_code": "Z", "message": "失败 C"},
            ],
            "consecutive_failed_tools": 3,
        }
    )

    assert state["agent_decision"]["type"] == "final_answer"
    assert state["assistant_answer"].startswith("本地工具已连续 3 批次未能取得有效结果")
    # 触发熔断后必须把计数清零，防止下一轮立刻又被判定为熔断。
    assert state["consecutive_failed_tools"] == 0


def test_tools_node_resets_consecutive_failed_on_success(session_factory, monkeypatch):
    """工具成功一次必须把连续失败计数清零，避免与早期失败叠加触发熔断。"""

    def fake_run(state, *, action, session_factory, allowed_tool_names, step_index=None):  # noqa: ARG001
        # 模拟工具返回成功观测，附加到 state.tool_observations。
        obs = {
            "tool_call_id": action["tool_call_id"],
            "tool_name": action["tool_name"],
            "arguments": action.get("arguments") or {},
            "ok": True,
            "data": {},
            "error_code": "",
            "message": "",
            "blocked": False,
        }
        return {"tool_observations": [*state.get("tool_observations", []), obs]}

    monkeypatch.setattr(
        "app.agent.graphs.memory_chat.nodes._run_agent_tool_action",
        fake_run,
    )

    update = build_tools_node(session_factory)(
        {
            "consecutive_failed_tools": 2,
            "agent_decision": {
                "type": "tool_call",
                "tool_calls": [{"id": "c-1", "name": "list_dir", "args": {"path": "."}}],
            },
            "tool_observations": [],
        }
    )

    assert update["consecutive_failed_tools"] == 0


def test_tools_node_preserves_request_user_input_arguments(session_factory, monkeypatch):
    """request_user_input 不是 Local Operator 工具，参数不能被通用 normalizer 清空。"""

    captured_actions: list[dict] = []

    def fake_run(state, *, action, session_factory, allowed_tool_names, step_index=None):  # noqa: ARG001
        captured_actions.append(action)
        obs = {
            "tool_call_id": action["tool_call_id"],
            "tool_name": action["tool_name"],
            "arguments": action.get("arguments") or {},
            "ok": True,
            "data": {},
            "error_code": "",
            "message": "用户选择：/home/wujie/test.txt",
            "blocked": False,
        }
        return {
            "tool_observations": [*state.get("tool_observations", []), obs],
            "turn_messages": list(state.get("turn_messages") or []),
            "thought_events": list(state.get("thought_events") or []),
        }

    monkeypatch.setattr(
        "app.agent.graphs.memory_chat.nodes._run_agent_tool_action",
        fake_run,
    )

    update = build_tools_node(session_factory)(
        {
            "agent_decision": {
                "type": "tool_call",
                "tool_calls": [
                    {
                        "id": "choice-1",
                        "name": "request_user_input",
                        "args": {
                            "question": "test.txt 应该创建在哪个目录下？",
                            "options": [
                                {
                                    "label": "Home 目录",
                                    "value": "/home/wujie/test.txt",
                                    "description": "不污染 AiMemo 仓库。",
                                },
                                {
                                    "label": "AiMemo 临时目录",
                                    "value": "/home/wujie/project/AiMemo/data/test.txt",
                                    "description": "放在项目运行数据目录。",
                                },
                            ],
                            "allow_other": True,
                        },
                    }
                ],
            },
            "tool_observations": [],
            "turn_messages": [],
            "thought_events": [],
            "prompt_context": "",
        }
    )

    assert captured_actions[0]["arguments"]["question"] == "test.txt 应该创建在哪个目录下？"
    assert len(captured_actions[0]["arguments"]["options"]) == 2
    assert captured_actions[0]["arguments"]["options"][0]["label"] == "Home 目录"
    assert update["consecutive_failed_tools"] == 0


def test_request_user_input_rejects_empty_question_instead_of_interrupting(session_factory):
    """空参数不能弹出“需要你补充一个选择/继续”的兜底选择框。"""

    update = _run_agent_tool_action(
        {
            "tool_observations": [],
            "turn_messages": [],
            "thought_events": [],
            "tool_budget": 3,
        },
        action={
            "tool_call_id": "choice-empty",
            "tool_name": "request_user_input",
            "arguments": {},
        },
        session_factory=session_factory,
        allowed_tool_names={"request_user_input"},
        step_index=1,
    )

    observation = update["tool_observations"][0]
    assert observation["ok"] is False
    assert observation["error_code"] == "INVALID_ARGUMENT"
    assert "缺少具体问题" in observation["message"]
    assert "需要你补充一个选择" not in observation["message"]


def test_request_user_input_normalizes_multi_question_resume():
    """多问题选择应保留逐题答案，避免 agent 分不清答案对应的问题。"""

    request = {
        "kind": "user_input",
        "request_id": "choice-multi",
        "questions": [
            {
                "id": "target_dir",
                "question": "项目应该创建在哪个目录下？",
                "selection_mode": "single",
                "options": [
                    {"id": "home", "label": "Home 下新建", "value": "E:/demo/blog"},
                    {"id": "repo", "label": "AiMemo 子目录", "value": "E:/Ai记/data/blog"},
                ],
            },
            {
                "id": "program_kind",
                "question": "这个程序要实现什么功能？",
                "selection_mode": "single",
                "options": [
                    {"id": "hello", "label": "Hello World", "value": "输出 Hello World"},
                    {"id": "custom", "label": "自定义", "value": ""},
                ],
            },
        ],
    }

    payload = {
        "request_id": "choice-multi",
        "question_answers": [
            {
                "question_id": "target_dir",
                "selected_option_id": "home",
                "selected_option_ids": ["home"],
                "answer": "E:/demo/blog",
            },
            {
                "question_id": "program_kind",
                "selected_option_id": "other",
                "selected_option_ids": ["other"],
                "answer": "生成 8 个随机数",
                "other_text": "生成 8 个随机数",
            },
        ],
    }

    normalized = _normalize_user_input_resume(payload, request)

    assert normalized["selected_option_ids"] == ["home", "other"]
    assert normalized["question_answers"][0]["question_id"] == "target_dir"
    assert normalized["question_answers"][0]["answer"] == "E:/demo/blog"
    assert normalized["question_answers"][1]["question_id"] == "program_kind"
    assert normalized["question_answers"][1]["is_other"] is True
    assert "项目应该创建在哪个目录下" in normalized["answer"]


def test_tools_node_executes_consecutive_reads_in_parallel(session_factory, monkeypatch):
    """连续多个 READ 工具必须真并行（与 Claude Code 行为对齐）。

    用 threading.Barrier(N) 强同步：如果是串行执行，第一个线程进入 barrier 后
    没有第二个线程到达，barrier.wait() 会超时抛 BrokenBarrierError，测试就失败。
    只有真正并行执行才能让 3 个线程同时到达 barrier 并通过。
    """

    import threading

    barrier = threading.Barrier(3, timeout=3.0)

    def fake_run(state, *, action, session_factory, allowed_tool_names, step_index=None):  # noqa: ARG001
        barrier.wait()
        obs = {
            "tool_call_id": action["tool_call_id"],
            "tool_name": action["tool_name"],
            "arguments": action.get("arguments") or {},
            "ok": True,
            "data": {},
            "error_code": "",
            "message": "ok",
            "blocked": False,
        }
        return {
            "tool_observations": [*state.get("tool_observations", []), obs],
            "tool_budget": max(int(state.get("tool_budget") or 0) - 1, 0),
            "turn_messages": list(state.get("turn_messages") or []),
            "thought_events": list(state.get("thought_events") or []),
        }

    monkeypatch.setattr(
        "app.agent.graphs.memory_chat.nodes._run_agent_tool_action",
        fake_run,
    )

    update = build_tools_node(session_factory)(
        {
            "agent_decision": {
                "type": "tool_call",
                "tool_calls": [
                    {"id": "r-1", "name": "read_file", "args": {"path": "a.txt"}},
                    {"id": "r-2", "name": "read_file", "args": {"path": "b.txt"}},
                    {"id": "r-3", "name": "read_file", "args": {"path": "c.txt"}},
                ],
            },
            "tool_observations": [],
            "tool_budget": 5,
            "turn_messages": [],
            "thought_events": [],
            "prompt_context": "",
        }
    )

    observations = update["tool_observations"]
    assert len(observations) == 3
    # 即使是并行执行，结果也必须按原 tool_calls 顺序回写，否则会和 AIMessage 的
    # tool_call_id 对不上、ReAct 下一步推理就会拿错 ToolMessage。
    assert [obs["tool_call_id"] for obs in observations] == ["r-1", "r-2", "r-3"]
    assert all(obs["ok"] for obs in observations)


def test_tools_node_keeps_write_serialized_against_surrounding_reads(session_factory, monkeypatch):
    """混合 [READ, READ, WRITE, READ] 时 WRITE 必须把前后 READ 段切开：

    - r-1 / r-2 是连续 READ → 同一 batch 内并行
    - w-1 是 WRITE → 单独串行
    - r-3 又是新的 READ 段 → 必须等 w-1 完成后才开始

    这是 read-before-write 与 exec 命令依赖语义所要求的最小保证。
    """

    import threading
    import time

    log: list[str] = []
    lock = threading.Lock()

    def fake_run(state, *, action, session_factory, allowed_tool_names, step_index=None):  # noqa: ARG001
        call_id = action["tool_call_id"]
        with lock:
            log.append(f"start:{call_id}")
        time.sleep(0.03)
        with lock:
            log.append(f"end:{call_id}")
        obs = {
            "tool_call_id": call_id,
            "tool_name": action["tool_name"],
            "arguments": action.get("arguments") or {},
            "ok": True,
            "data": {},
            "error_code": "",
            "message": "ok",
            "blocked": False,
        }
        return {
            "tool_observations": [*state.get("tool_observations", []), obs],
            "tool_budget": max(int(state.get("tool_budget") or 0) - 1, 0),
            "turn_messages": list(state.get("turn_messages") or []),
            "thought_events": list(state.get("thought_events") or []),
        }

    monkeypatch.setattr(
        "app.agent.graphs.memory_chat.nodes._run_agent_tool_action",
        fake_run,
    )

    update = build_tools_node(session_factory)(
        {
            "agent_decision": {
                "type": "tool_call",
                "tool_calls": [
                    {"id": "r-1", "name": "read_file", "args": {"path": "a.txt"}},
                    {"id": "r-2", "name": "read_file", "args": {"path": "b.txt"}},
                    {"id": "w-1", "name": "write_file", "args": {"path": "c.txt", "content": "x"}},
                    {"id": "r-3", "name": "read_file", "args": {"path": "d.txt"}},
                ],
            },
            "tool_observations": [],
            "tool_budget": 10,
            "turn_messages": [],
            "thought_events": [],
            "prompt_context": "",
        }
    )

    observations = update["tool_observations"]
    assert [obs["tool_call_id"] for obs in observations] == ["r-1", "r-2", "w-1", "r-3"]

    def idx(event: str) -> int:
        return log.index(event)

    # w-1 必须严格在 r-1 / r-2 都结束之后才开始
    assert idx("end:r-1") < idx("start:w-1")
    assert idx("end:r-2") < idx("start:w-1")
    # r-3 必须严格在 w-1 结束之后才开始
    assert idx("end:w-1") < idx("start:r-3")
