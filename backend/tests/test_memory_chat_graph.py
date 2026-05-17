from pathlib import Path

from sqlmodel import select

from app.agent.graphs.memory_chat.graph import build_memory_chat_graph, run_memory_chat_graph
from app.agent.graphs.memory_chat.nodes import RetrievalPlan
from app.agent.graphs.memory_chat.nodes import build_memory_chat_answer_system_prompt
from app.agent.graphs.memory_chat.nodes import default_retrieval_planner
from app.models.chat_message import ChatMessage
from app.models.long_term_memory import LongTermMemory
from app.rag.hashing import content_hash
from app.rag.search import NoteSearchResult
from app.schemas.conversation import ConversationCreate
from app.services.conversation_service import create_conversation


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
    assert "L0 当前用户输入" in result["prompt_context"]
    assert "本轮未查询个人知识库" in result["prompt_context"]
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[0].content == "1+1 等于几？"
    assert messages[1].content == "1+1 等于 2。"
    assert messages[1].parent_id == messages[0].id
    assert messages[0].checkpoint_id
    assert messages[0].checkpoint_id == messages[1].checkpoint_id


def test_memory_chat_graph_main_flow_is_flat_context_worker_graph(session_factory):
    graph = build_memory_chat_graph(session_factory=session_factory)
    mermaid = graph.compile().get_graph().draw_mermaid()

    assert "load_turn_state" in mermaid
    assert "dispatch_context_workers" in mermaid
    assert "build_l3_retrieved_memory" in mermaid
    assert "merge_prompt_context" in mermaid
    assert "plan_retrieval" not in mermaid
    assert "retrieve_notes" not in mermaid
    assert "grade_retrieval" not in mermaid


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
