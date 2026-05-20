from pathlib import Path

from sqlmodel import select

from app.agent.graphs.conversation_memory.graph import run_conversation_memory_graph
from app.agent.graphs.conversation_memory.nodes import parse_memory_extraction_response
from app.jobs.models import GraphName, JobType
from app.jobs.queue import enqueue_job
from app.models.chat_message import ChatMessage
from app.models.long_term_memory import LongTermMemory
from app.models.note import utc_now
from app.schemas.conversation import ConversationCreate
from app.services.conversation_service import create_conversation
from app.services.memory_service import build_memory_content_hash


def test_conversation_memory_graph_writes_high_value_memory(
    session,
    session_factory,
    tmp_path: Path,
):
    conversation = create_conversation(session, ConversationCreate(title="长期记忆"))
    user = _add_message(session, conversation.id, "user", "请记住，我不吃香菜。")
    assistant = _add_message(session, conversation.id, "assistant", "好的，我记住了。", user.id)
    job = _enqueue_memory_job(session, conversation.id, user.id, assistant.id)

    def fake_extractor(messages):
        assert [message["role"] for message in messages] == ["user", "assistant"]
        return {
            "memories": [
                {
                    "should_write": True,
                    "category": "preference",
                    "content": "用户不吃香菜。",
                    "summary": "不吃香菜",
                    "importance": 0.9,
                    "confidence": 0.95,
                }
            ]
        }

    run_conversation_memory_graph(
        job,
        session_factory=session_factory,
        checkpoint_path=str(tmp_path / "checkpoints.db"),
        memory_extractor=fake_extractor,
    )

    session.expire_all()
    memories = session.exec(select(LongTermMemory)).all()
    assert len(memories) == 1
    assert memories[0].content == "用户不吃香菜。"
    assert memories[0].category == "preference"
    assert memories[0].source_id == assistant.id


def test_conversation_memory_graph_filters_low_confidence_memory(
    session,
    session_factory,
    tmp_path: Path,
):
    conversation = create_conversation(session, ConversationCreate(title="过滤记忆"))
    user = _add_message(session, conversation.id, "user", "今天可能会下雨。")
    assistant = _add_message(session, conversation.id, "assistant", "我不确定。", user.id)
    job = _enqueue_memory_job(session, conversation.id, user.id, assistant.id)

    def fake_extractor(messages):
        return {
            "memories": [
                {
                    "should_write": True,
                    "category": "fact",
                    "content": "用户今天可能遇到下雨。",
                    "summary": "可能下雨",
                    "importance": 0.4,
                    "confidence": 0.4,
                }
            ]
        }

    run_conversation_memory_graph(
        job,
        session_factory=session_factory,
        checkpoint_path=str(tmp_path / "checkpoints.db"),
        memory_extractor=fake_extractor,
    )

    assert session.exec(select(LongTermMemory)).all() == []


def test_conversation_memory_graph_resumes_after_extract_without_recalling_llm(
    session,
    session_factory,
    tmp_path: Path,
):
    conversation = create_conversation(session, ConversationCreate(title="记忆恢复"))
    user = _add_message(session, conversation.id, "user", "我的长期目标是写完 Ai 记。")
    assistant = _add_message(session, conversation.id, "assistant", "这个目标很清楚。", user.id)
    job = _enqueue_memory_job(session, conversation.id, user.id, assistant.id)
    checkpoint_path = tmp_path / "checkpoints.db"
    calls: list[int] = []

    def fake_extractor(messages):
        calls.append(len(messages))
        return {
            "memories": [
                {
                    "should_write": True,
                    "category": "goal",
                    "content": "用户的长期目标是写完 Ai 记。",
                    "summary": "写完 Ai 记",
                    "importance": 0.95,
                    "confidence": 0.9,
                }
            ]
        }

    run_conversation_memory_graph(
        job,
        session_factory=session_factory,
        checkpoint_path=str(checkpoint_path),
        memory_extractor=fake_extractor,
        interrupt_after=["extract_memories"],
    )
    assert session.exec(select(LongTermMemory)).all() == []

    run_conversation_memory_graph(
        job,
        session_factory=session_factory,
        checkpoint_path=str(checkpoint_path),
        memory_extractor=fake_extractor,
    )

    session.expire_all()
    memories = session.exec(select(LongTermMemory)).all()
    assert calls == [2]
    assert len(memories) == 1
    assert memories[0].content == "用户的长期目标是写完 Ai 记。"


def test_conversation_memory_graph_skips_semantic_duplicate_memory(
    session,
    session_factory,
    tmp_path: Path,
):
    """措辞不同但语义重复的长期目标不应重复进入 L4。"""

    existing = LongTermMemory(
        level=4,
        category="goal",
        content="用户计划开发名为 Ai 记 的智能化笔记软件。",
        summary="开发 Ai 记智能笔记软件",
        importance=0.9,
        confidence=1.0,
        source_type="chat_message",
        source_id=18,
        status="active",
        content_hash=build_memory_content_hash(
            "goal",
            "用户计划开发名为 Ai 记 的智能化笔记软件。",
        ),
        updated_at=utc_now(),
    )
    session.add(existing)
    session.commit()

    conversation = create_conversation(session, ConversationCreate(title="重复记忆"))
    user = _add_message(session, conversation.id, "user", "我正在计划开发名为 Ai 记 的智能化笔记软件。")
    assistant = _add_message(session, conversation.id, "assistant", "这很适合作为长期目标。", user.id)
    job = _enqueue_memory_job(session, conversation.id, user.id, assistant.id)

    def fake_extractor(messages):
        return {
            "memories": [
                {
                    "should_write": True,
                    "category": "goal",
                    "content": "用户正在计划开发名为 Ai 记 的智能化笔记软件。",
                    "summary": "计划开发 Ai 记笔记软件",
                    "importance": 0.9,
                    "confidence": 1.0,
                }
            ]
        }

    run_conversation_memory_graph(
        job,
        session_factory=session_factory,
        checkpoint_path=str(tmp_path / "checkpoints.db"),
        memory_extractor=fake_extractor,
    )

    session.expire_all()
    memories = session.exec(select(LongTermMemory)).all()
    assert len(memories) == 1
    assert memories[0].id == existing.id
    assert memories[0].content == "用户计划开发名为 Ai 记 的智能化笔记软件。"


def test_conversation_memory_graph_updates_cross_category_memory_key_conflict(
    session,
    session_factory,
    tmp_path: Path,
):
    """同一 memory_key 即使 category 不同，也应更新旧记忆而不是新增重复记忆。"""

    existing = LongTermMemory(
        level=4,
        category="identity",
        memory_key="user.preferred_name",
        content="用户希望被称呼为小刘。",
        summary="用户昵称是小刘",
        importance=0.9,
        confidence=1.0,
        source_type="chat_message",
        source_id=44,
        status="active",
        content_hash=build_memory_content_hash("identity", "用户希望被称呼为小刘。"),
        updated_at=utc_now(),
    )
    session.add(existing)
    session.commit()
    session.refresh(existing)

    conversation = create_conversation(session, ConversationCreate(title="称呼更新"))
    user = _add_message(session, conversation.id, "user", "以后叫我家炫，不要叫我小刘。")
    assistant = _add_message(session, conversation.id, "assistant", "好的，我会称呼你为家炫。", user.id)
    job = _enqueue_memory_job(session, conversation.id, user.id, assistant.id)
    judge_seen_ids: list[int] = []

    def fake_extractor(messages):
        return {
            "memories": [
                {
                    "should_write": True,
                    "category": "preference",
                    "memory_key": "user.preferred_name",
                    "content": "用户希望被称呼为家炫，而不是小刘。",
                    "summary": "偏好称呼：家炫",
                    "importance": 0.9,
                    "confidence": 1.0,
                }
            ]
        }

    def fake_judge(candidate, existing_memories):
        judge_seen_ids.extend(memory.id for memory in existing_memories if memory.id is not None)
        return {
            "action": "update",
            "existing_memory_id": existing.id,
            "category": "preference",
            "memory_key": "user.preferred_name",
            "content": "用户希望被称呼为家炫，而不是小刘。",
            "summary": "偏好称呼：家炫",
            "importance": 0.9,
            "confidence": 1.0,
            "reason": "同一称呼槽位出现更新。",
        }

    run_conversation_memory_graph(
        job,
        session_factory=session_factory,
        checkpoint_path=str(tmp_path / "checkpoints.db"),
        memory_extractor=fake_extractor,
        consolidation_judge=fake_judge,
    )

    session.expire_all()
    memories = session.exec(select(LongTermMemory)).all()
    assert judge_seen_ids == [existing.id]
    assert len(memories) == 1
    assert memories[0].id == existing.id
    assert memories[0].category == "preference"
    assert memories[0].memory_key == "user.preferred_name"
    assert memories[0].content == "用户希望被称呼为家炫，而不是小刘。"


def test_conversation_memory_graph_resumes_after_consolidation_without_rejudging(
    session,
    session_factory,
    tmp_path: Path,
):
    """consolidation_result 进入 checkpoint 后，恢复写入不应重复执行归并 judge。"""

    existing = LongTermMemory(
        level=4,
        category="preference",
        content="用户喜欢用深色主题。",
        summary="喜欢深色主题",
        importance=0.8,
        confidence=0.9,
        source_type="chat_message",
        source_id=1,
        status="active",
        content_hash=build_memory_content_hash("preference", "用户喜欢用深色主题。"),
        updated_at=utc_now(),
    )
    session.add(existing)
    session.commit()
    session.refresh(existing)

    conversation = create_conversation(session, ConversationCreate(title="归并恢复"))
    user = _add_message(session, conversation.id, "user", "我喜欢深色模式，尤其是编辑器。")
    assistant = _add_message(session, conversation.id, "assistant", "我会记住。", user.id)
    job = _enqueue_memory_job(session, conversation.id, user.id, assistant.id)
    checkpoint_path = tmp_path / "checkpoints.db"
    judge_calls: list[int] = []

    def fake_extractor(messages):
        return {
            "memories": [
                {
                    "should_write": True,
                    "category": "preference",
                    "content": "用户喜欢深色模式，尤其是编辑器。",
                    "summary": "喜欢编辑器深色模式",
                    "importance": 0.85,
                    "confidence": 0.95,
                }
            ]
        }

    def fake_judge(candidate, existing_memories):
        judge_calls.append(len(existing_memories))
        return {
            "action": "update",
            "existing_memory_id": existing.id,
            "category": "preference",
            "content": "用户喜欢深色模式，尤其是编辑器。",
            "summary": "喜欢编辑器深色模式",
            "importance": 0.85,
            "confidence": 0.95,
            "reason": "新记忆补充了编辑器偏好。",
        }

    run_conversation_memory_graph(
        job,
        session_factory=session_factory,
        checkpoint_path=str(checkpoint_path),
        memory_extractor=fake_extractor,
        consolidation_judge=fake_judge,
        interrupt_after=["consolidate_memories"],
    )
    session.expire_all()
    assert session.get(LongTermMemory, existing.id).content == "用户喜欢用深色主题。"

    run_conversation_memory_graph(
        job,
        session_factory=session_factory,
        checkpoint_path=str(checkpoint_path),
        memory_extractor=fake_extractor,
        consolidation_judge=fake_judge,
    )

    session.expire_all()
    memories = session.exec(select(LongTermMemory)).all()
    assert judge_calls == [1]
    assert len(memories) == 1
    assert memories[0].content == "用户喜欢深色模式，尤其是编辑器。"
    assert memories[0].importance == 0.85
    assert memories[0].confidence == 0.95


def test_parse_memory_extraction_response_falls_back_on_invalid_json():
    """模型返回非严格 JSON 时，长期记忆 job 应降级为空结果而不是失败。"""

    payload = parse_memory_extraction_response(
        '{"memories":[{"should_write":true "content":"缺少逗号"}]}'
    )

    assert payload == {"memories": []}


def test_parse_memory_extraction_response_normalizes_missing_memories():
    """缺少 memories 字段时按空记忆处理，保持写入节点输入稳定。"""

    payload = parse_memory_extraction_response('{"note":"nothing to write"}')

    assert payload == {"memories": []}


def _enqueue_memory_job(session, conversation_id: int, user_id: int, assistant_id: int):
    job = enqueue_job(
        session,
        job_type=JobType.CONVERSATION_MEMORY.value,
        graph_name=GraphName.CONVERSATION_MEMORY.value,
        payload={
            "conversation_id": conversation_id,
            "user_message_id": user_id,
            "assistant_message_id": assistant_id,
        },
    )
    session.commit()
    session.refresh(job)
    return job


def _add_message(
    session,
    conversation_id: int,
    role: str,
    content: str,
    parent_id: int | None = None,
) -> ChatMessage:
    message = ChatMessage(
        conversation_id=conversation_id,
        role=role,
        content=content,
        parent_id=parent_id,
    )
    session.add(message)
    session.commit()
    session.refresh(message)
    return message
