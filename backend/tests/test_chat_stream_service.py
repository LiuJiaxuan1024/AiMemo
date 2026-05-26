import json
import threading
import time
from pathlib import Path

from sqlmodel import select

from app.models.chat_message import ChatMessage
from app.models.chat_turn import ChatTurn
from app.schemas.conversation import ConversationCreate
from app.services import chat_turn_buffer
from app.services.chat_service import (
    stream_conversation_chat_events,
    stream_conversation_chat_resume_events,
    stream_existing_turn_events,
)
from app.services.chat_turn_service import (
    get_chat_turn_graph_by_turn,
    list_active_chat_turns,
)
from app.services.conversation_service import create_conversation


def test_stream_chat_creates_turn_and_messages_before_graph_finishes(
    session,
    session_factory,
    tmp_path: Path,
    monkeypatch,
):
    """流式对话开始时就应落库消息，浏览器刷新后不会丢失本轮对话。"""

    conversation = create_conversation(session, ConversationCreate(title="流式对话"))
    checkpoint_path = tmp_path / "checkpoints.db"

    def fake_stream_memory_chat_graph(**kwargs):
        user_message_id = int(kwargs["user_message_id"])
        assistant_message_id = int(kwargs["assistant_message_id"])
        yield {"event": "node", "node": "load_turn_state", "state": {}}
        yield {
            "event": "node",
            "node": "build_l3_retrieved_memory",
            "state": {
                "retrieval_query": "测试刷新恢复",
                "retrieval_debug": {
                    "planner_ms": 7,
                    "retriever_ms": 11,
                    "total_ms": 18,
                    "planner_source": "test",
                }
            },
        }
        yield {"event": "answer_delta", "node": "agent", "content": "你好", "metadata": {}}
        yield {"event": "answer_delta", "node": "agent", "content": "，世界", "metadata": {}}
        yield {"event": "node", "node": "persist_messages", "state": {}}
        yield {
            "event": "done",
            "node": "",
            "state": {
                "user_message_id": user_message_id,
                "assistant_message_id": assistant_message_id,
                "graph_checkpoint_id": "checkpoint-1",
                "needs_retrieval": False,
                "needs_query_rewrite": False,
                "retrieval_query": "",
                "retrieval_grade": "none",
                "retrieval_grade_reason": "",
                "retrieval_reason": "",
                "retrieved_chunks": [],
            },
        }

    monkeypatch.setattr(
        "app.services.chat_service.stream_memory_chat_graph",
        fake_stream_memory_chat_graph,
    )

    events = stream_conversation_chat_events(
        conversation.id,
        message="测试刷新恢复",
        session_factory=session_factory,
        checkpoint_path=str(checkpoint_path),
    )
    first_event = _parse_sse(next(events))

    assert first_event["event"] == "turn"
    assert first_event["data"]["turn_id"] > 0
    assert first_event["data"]["user_message"]["content"] == "测试刷新恢复"
    assert first_event["data"]["assistant_message"]["status"] == "streaming"
    assert first_event["data"]["assistant_message"]["turn_id"] == first_event["data"]["turn_id"]

    messages = session.exec(select(ChatMessage).order_by(ChatMessage.id)).all()
    assert [message.role for message in messages] == ["user", "assistant"]
    assert messages[0].status == "completed"
    assert messages[1].status == "streaming"

    remaining_events = [_parse_sse(event) for event in events]
    assert [event["event"] for event in remaining_events] == [
        "node",
        "node",
        "node",
        "answer_delta",
        "answer_delta",
        "node",
        "done",
    ]

    session.expire_all()
    assistant = session.get(ChatMessage, messages[1].id)
    turn = session.get(ChatTurn, first_event["data"]["turn_id"])
    assert assistant is not None
    assert assistant.content == "你好，世界"
    assert assistant.status == "completed"
    assert turn is not None
    assert turn.status == "completed"
    assert turn.assistant_message_id == assistant.id
    done_event = remaining_events[-1]
    assert done_event["data"]["response"]["assistant_message"]["turn_id"] == first_event["data"]["turn_id"]
    debug_payload = json.loads(turn.debug_payload)
    assert debug_payload["events"]["turn_created"] >= 0
    assert debug_payload["events"]["turn_completed"] >= 0
    assert debug_payload["summary"]["first_answer_token_ms"] is not None
    assert debug_payload["summary"]["answer_token_events"] == 2
    assert debug_payload["summary"]["answer_chars"] == len("你好，世界")
    assert debug_payload["nodes"]["build_l3_retrieved_memory"]["retrieval_debug"]["planner_ms"] == 7
    assert debug_payload["nodes"]["build_l3_retrieved_memory"]["state"]["retrieval_query"] == "测试刷新恢复"


def test_chat_turn_graph_can_be_read_while_turn_is_running(session, session_factory):
    """运行中的 turn 也能通过 turn_id 打开 graph 调试视图。"""

    conversation = create_conversation(session, ConversationCreate(title="调试入口"))
    turn = ChatTurn(
        conversation_id=conversation.id,
        thread_id=f"conversation:{conversation.id}",
        status="running",
        node_statuses=json.dumps(
            {
                "load_turn_state": "succeeded",
                "dispatch_context_workers": "running",
            },
            ensure_ascii=False,
        ),
        debug_payload=json.dumps(
            {
                "events": {"turn_created": 0},
                "summary": {"first_answer_token_ms": 123},
                "nodes": {},
            },
            ensure_ascii=False,
        ),
    )
    session.add(turn)
    session.commit()
    session.refresh(turn)

    graph = get_chat_turn_graph_by_turn(
        session,
        conversation_id=conversation.id,
        turn_id=turn.id or 0,
    )

    assert graph.status == "running"
    assert graph.node_statuses["dispatch_context_workers"] == "running"
    assert graph.debug_payload["summary"]["first_answer_token_ms"] == 123
    assert "dispatch_context_workers" in graph.mermaid


def test_stream_chat_keeps_running_after_client_disconnect(
    session,
    session_factory,
    tmp_path: Path,
    monkeypatch,
):
    """浏览器切走/刷新只应关闭 SSE 通道，不应终止后台 graph 执行。"""

    chat_turn_buffer.reset_for_tests()
    conversation = create_conversation(session, ConversationCreate(title="不被切走打断"))
    checkpoint_path = tmp_path / "checkpoints.db"

    # 让 graph 在第一段回答后停下来等测试 "断开"，再放行剩余事件。
    proceed_after_disconnect = threading.Event()

    def fake_stream_memory_chat_graph(**kwargs):
        user_message_id = int(kwargs["user_message_id"])
        assistant_message_id = int(kwargs["assistant_message_id"])
        yield {"event": "node", "node": "load_turn_state", "state": {}}
        yield {"event": "answer_delta", "node": "agent", "content": "前", "metadata": {}}
        # 这里 wait 模拟图还在跑、HTTP 连接已经被前端关闭。
        proceed_after_disconnect.wait(timeout=5)
        yield {"event": "answer_delta", "node": "agent", "content": "后", "metadata": {}}
        yield {"event": "node", "node": "persist_messages", "state": {}}
        yield {
            "event": "done",
            "node": "",
            "state": {
                "user_message_id": user_message_id,
                "assistant_message_id": assistant_message_id,
                "graph_checkpoint_id": "checkpoint-resumable",
                "needs_retrieval": False,
                "needs_query_rewrite": False,
                "retrieval_query": "",
                "retrieval_grade": "none",
                "retrieval_grade_reason": "",
                "retrieval_reason": "",
                "retrieved_chunks": [],
            },
        }

    monkeypatch.setattr(
        "app.services.chat_service.stream_memory_chat_graph",
        fake_stream_memory_chat_graph,
    )

    events = stream_conversation_chat_events(
        conversation.id,
        message="断开后继续跑",
        session_factory=session_factory,
        checkpoint_path=str(checkpoint_path),
    )
    first_event = _parse_sse(next(events))
    turn_id = first_event["data"]["turn_id"]

    # "断开" SSE：关闭生成器丢弃订阅，但后台线程不应被影响。
    events.close()

    # 放行后台 graph 继续跑完。
    proceed_after_disconnect.set()

    buffer = chat_turn_buffer.get(turn_id)
    assert buffer is not None
    _wait_until(lambda: buffer.done, timeout=5.0)

    session.expire_all()
    turn = session.get(ChatTurn, turn_id)
    assert turn is not None
    assert turn.status == "completed"
    assert turn.assistant_message_id is not None
    assistant = session.get(ChatMessage, turn.assistant_message_id)
    assert assistant is not None
    # 后续 token 在断开后才到达，仍然应该被落库。
    assert assistant.content == "前后"
    assert assistant.status == "completed"


def test_stream_chat_interrupts_and_resumes_same_turn(
    session,
    session_factory,
    tmp_path: Path,
    monkeypatch,
):
    """request_user_input interrupt 应落库为 interrupted，并能用 resume 接着跑完同一轮。"""

    chat_turn_buffer.reset_for_tests()
    conversation = create_conversation(session, ConversationCreate(title="用户选择"))
    checkpoint_path = tmp_path / "checkpoints.db"
    seen_resume_payloads: list[dict] = []

    def fake_stream_memory_chat_graph(**kwargs):
        user_message_id = int(kwargs["user_message_id"])
        assistant_message_id = int(kwargs["assistant_message_id"])
        resume_payload = kwargs.get("resume_payload")
        if resume_payload is None:
            yield {
                "event": "node",
                "node": "tools",
                "state": {"agent_step_index": 1},
            }
            yield {
                "event": "interrupt",
                "node": "",
                "interrupt": {
                    "id": "choice-1",
                    "value": {
                        "kind": "user_input",
                        "request_id": "choice-1",
                        "question": "新项目要写到哪里？",
                        "selection_mode": "single",
                        "allow_other": True,
                        "options": [
                            {
                                "id": "home",
                                "label": "Home 下新建",
                                "value": "/home/wujie/demo",
                                "description": "保持 AiMemo 仓库干净。",
                                "recommended": True,
                            }
                        ],
                    },
                },
                "state": {
                    "user_message_id": user_message_id,
                    "assistant_message_id": assistant_message_id,
                    "graph_checkpoint_id": "checkpoint-interrupt",
                },
            }
            return

        seen_resume_payloads.append(dict(resume_payload))
        yield {"event": "node", "node": "tools", "state": {"agent_step_index": 1}}
        yield {"event": "answer_delta", "node": "agent", "content": "已按你的选择继续。", "metadata": {}}
        yield {"event": "node", "node": "persist_messages", "state": {}}
        yield {
            "event": "done",
            "node": "",
            "state": {
                "user_message_id": user_message_id,
                "assistant_message_id": assistant_message_id,
                "assistant_answer": "已按你的选择继续。",
                "graph_checkpoint_id": "checkpoint-done",
                "needs_retrieval": False,
                "needs_query_rewrite": False,
                "retrieval_query": "",
                "retrieval_grade": "none",
                "retrieval_grade_reason": "",
                "retrieval_reason": "",
                "retrieved_chunks": [],
            },
        }

    monkeypatch.setattr(
        "app.services.chat_service.stream_memory_chat_graph",
        fake_stream_memory_chat_graph,
    )

    interrupted_events = [
        _parse_sse(event)
        for event in stream_conversation_chat_events(
            conversation.id,
            message="帮我写一个项目",
            session_factory=session_factory,
            checkpoint_path=str(checkpoint_path),
        )
    ]

    assert [event["event"] for event in interrupted_events] == ["turn", "node", "interrupt"]
    turn_id = interrupted_events[0]["data"]["turn_id"]
    interrupt_request = interrupted_events[-1]["data"]["request"]
    assert interrupt_request["question"] == "新项目要写到哪里？"
    assert interrupt_request["options"][0]["id"] == "home"
    assert interrupt_request["other_option"]["id"] == "other"

    session.expire_all()
    turn = session.get(ChatTurn, turn_id)
    assert turn is not None
    assert turn.status == "interrupted"
    assert turn.checkpoint_id == "checkpoint-interrupt"
    assistant = session.get(ChatMessage, turn.assistant_message_id)
    assert assistant is not None
    assert assistant.status == "interrupted"

    active = list_active_chat_turns(session, conversation_id=conversation.id)
    assert [item.turn_id for item in active.items] == [turn_id]
    assert active.items[0].status == "interrupted"
    assert active.items[0].pending_interrupt is not None
    assert active.items[0].assistant_message is not None
    assert active.items[0].assistant_message.pending_interrupt is not None

    resumed_events = [
        _parse_sse(event)
        for event in stream_conversation_chat_resume_events(
            conversation.id,
            turn_id,
            resume_payload={
                "request_id": "choice-1",
                "selected_option_id": "home",
                "selected_option_ids": ["home"],
                "answer": "/home/wujie/demo",
            },
            session_factory=session_factory,
            checkpoint_path=str(checkpoint_path),
        )
    ]

    assert seen_resume_payloads == [
        {
            "request_id": "choice-1",
            "selected_option_id": "home",
            "selected_option_ids": ["home"],
            "answer": "/home/wujie/demo",
        }
    ]
    assert [event["event"] for event in resumed_events] == [
        "resume",
        "node",
        "node",
        "answer_delta",
        "node",
        "done",
    ]

    session.expire_all()
    turn = session.get(ChatTurn, turn_id)
    assert turn is not None
    assert turn.status == "completed"
    assistant = session.get(ChatMessage, turn.assistant_message_id)
    assert assistant is not None
    assert assistant.content == "已按你的选择继续。"
    assert assistant.status == "completed"


def test_stream_chat_normalizes_multi_question_interrupt(
    session,
    session_factory,
    tmp_path: Path,
    monkeypatch,
):
    """interrupt payload 支持 questions[]，同时保留旧版 question/options 兼容字段。"""

    chat_turn_buffer.reset_for_tests()
    conversation = create_conversation(session, ConversationCreate(title="多问题选择"))
    checkpoint_path = tmp_path / "checkpoints.db"

    def fake_stream_memory_chat_graph(**kwargs):
        yield {
            "event": "interrupt",
            "node": "",
            "interrupt": {
                "id": "choice-multi",
                "value": {
                    "kind": "user_input",
                    "request_id": "choice-multi",
                    "questions": [
                        {
                            "id": "target_dir",
                            "question": "项目应该创建在哪个目录下？",
                            "selection_mode": "single",
                            "options": [
                                {"id": "home", "label": "Home 下新建", "value": "E:/demo"},
                                {"id": "repo", "label": "AiMemo data", "value": "E:/Ai记/data/demo"},
                            ],
                        },
                        {
                            "id": "feature",
                            "question": "这个程序要实现什么功能？",
                            "selection_mode": "single",
                            "options": [
                                {"id": "hello", "label": "Hello World", "value": "hello"},
                                {"id": "random", "label": "随机数", "value": "random"},
                            ],
                        },
                    ],
                },
            },
            "state": {
                "user_message_id": int(kwargs["user_message_id"]),
                "assistant_message_id": int(kwargs["assistant_message_id"]),
                "graph_checkpoint_id": "checkpoint-interrupt",
            },
        }

    monkeypatch.setattr(
        "app.services.chat_service.stream_memory_chat_graph",
        fake_stream_memory_chat_graph,
    )

    events = [
        _parse_sse(event)
        for event in stream_conversation_chat_events(
            conversation.id,
            message="创建一个 test.cc",
            session_factory=session_factory,
            checkpoint_path=str(checkpoint_path),
        )
    ]

    request = events[-1]["data"]["request"]
    assert request["request_id"] == "choice-multi"
    assert len(request["questions"]) == 2
    assert request["question"] == "项目应该创建在哪个目录下？"
    assert request["options"][0]["id"] == "home"
    assert request["other_option"]["id"] == "other"


def test_stream_existing_turn_events_replays_completed_buffer(
    session,
    session_factory,
    tmp_path: Path,
    monkeypatch,
):
    """完成后立刻重连应能拿到从头到尾的完整事件流。"""

    chat_turn_buffer.reset_for_tests()
    conversation = create_conversation(session, ConversationCreate(title="重放完整流"))
    checkpoint_path = tmp_path / "checkpoints.db"

    def fake_stream_memory_chat_graph(**kwargs):
        user_message_id = int(kwargs["user_message_id"])
        assistant_message_id = int(kwargs["assistant_message_id"])
        yield {"event": "node", "node": "load_turn_state", "state": {}}
        yield {"event": "answer_delta", "node": "agent", "content": "你好", "metadata": {}}
        yield {"event": "node", "node": "persist_messages", "state": {}}
        yield {
            "event": "done",
            "node": "",
            "state": {
                "user_message_id": user_message_id,
                "assistant_message_id": assistant_message_id,
                "graph_checkpoint_id": "checkpoint-replay",
                "needs_retrieval": False,
                "needs_query_rewrite": False,
                "retrieval_query": "",
                "retrieval_grade": "none",
                "retrieval_grade_reason": "",
                "retrieval_reason": "",
                "retrieved_chunks": [],
            },
        }

    monkeypatch.setattr(
        "app.services.chat_service.stream_memory_chat_graph",
        fake_stream_memory_chat_graph,
    )

    events = stream_conversation_chat_events(
        conversation.id,
        message="重连",
        session_factory=session_factory,
        checkpoint_path=str(checkpoint_path),
    )
    primary = [_parse_sse(event) for event in events]
    turn_id = primary[0]["data"]["turn_id"]
    assert primary[-1]["event"] == "done"

    # 立刻新建一个订阅者：buffer 还在 retention 窗口内，应该把同一组事件再放一遍。
    replay = [_parse_sse(event) for event in stream_existing_turn_events(turn_id)]
    assert [event["event"] for event in replay] == [event["event"] for event in primary]
    assert replay[0]["data"]["turn_id"] == turn_id
    assert replay[-1]["event"] == "done"


def test_stream_existing_turn_events_reports_unavailable_after_cleanup(
    session,
    session_factory,
    tmp_path: Path,
    monkeypatch,
):
    """retention 过期后重连应给出明确的不可用提示，避免前端无限挂着。"""

    chat_turn_buffer.reset_for_tests()
    conversation = create_conversation(session, ConversationCreate(title="过期重连"))
    checkpoint_path = tmp_path / "checkpoints.db"

    def fake_stream_memory_chat_graph(**kwargs):
        user_message_id = int(kwargs["user_message_id"])
        assistant_message_id = int(kwargs["assistant_message_id"])
        yield {"event": "node", "node": "load_turn_state", "state": {}}
        yield {
            "event": "done",
            "node": "",
            "state": {
                "user_message_id": user_message_id,
                "assistant_message_id": assistant_message_id,
                "graph_checkpoint_id": None,
                "needs_retrieval": False,
                "needs_query_rewrite": False,
                "retrieval_query": "",
                "retrieval_grade": "none",
                "retrieval_grade_reason": "",
                "retrieval_reason": "",
                "retrieved_chunks": [],
            },
        }

    monkeypatch.setattr(
        "app.services.chat_service.stream_memory_chat_graph",
        fake_stream_memory_chat_graph,
    )

    events = stream_conversation_chat_events(
        conversation.id,
        message="过期",
        session_factory=session_factory,
        checkpoint_path=str(checkpoint_path),
    )
    primary = [_parse_sse(event) for event in events]
    turn_id = primary[0]["data"]["turn_id"]

    # 模拟 retention 已过：直接清空 buffer 注册表。
    chat_turn_buffer.reset_for_tests()
    replay = [_parse_sse(event) for event in stream_existing_turn_events(turn_id)]
    assert len(replay) == 1
    assert replay[0]["event"] == "turn_unavailable"
    assert replay[0]["data"]["turn_id"] == turn_id


def test_stream_chat_persists_exception_detail_when_worker_crashes(
    session,
    session_factory,
    tmp_path: Path,
    monkeypatch,
):
    """worker 异常时应把完整 traceback 写进 debug_payload，方便前端排障。"""

    chat_turn_buffer.reset_for_tests()
    conversation = create_conversation(session, ConversationCreate(title="失败堆栈"))
    checkpoint_path = tmp_path / "checkpoints.db"

    def fake_stream_memory_chat_graph(**kwargs):
        yield {"event": "node", "node": "load_turn_state", "state": {}}
        raise RuntimeError("boom from graph")

    monkeypatch.setattr(
        "app.services.chat_service.stream_memory_chat_graph",
        fake_stream_memory_chat_graph,
    )

    events = stream_conversation_chat_events(
        conversation.id,
        message="失败",
        session_factory=session_factory,
        checkpoint_path=str(checkpoint_path),
    )
    primary = [_parse_sse(event) for event in events]
    turn_id = primary[0]["data"]["turn_id"]

    assert primary[-1]["event"] == "error"
    assert "boom from graph" in primary[-1]["data"]["message"]
    assert primary[-1]["data"]["exception"]["type"] == "RuntimeError"
    assert "traceback" in primary[-1]["data"]["exception"]

    turn = session.get(ChatTurn, turn_id)
    assert turn is not None
    assert turn.status == "failed"
    assert "RuntimeError" in turn.error
    debug_payload = json.loads(turn.debug_payload)
    diagnostic = debug_payload["diagnostics"][-1]
    assert diagnostic["code"] == "CHAT_TURN_WORKER_CRASHED"
    assert diagnostic["exception"]["type"] == "RuntimeError"
    assert "boom from graph" in diagnostic["exception"]["message"]
    assert "traceback" in diagnostic["exception"]


def test_list_active_chat_turns_returns_running_and_interrupted_turns(session):
    """active-turns 接口返回 running/interrupted turn，按创建顺序排序。"""

    chat_turn_buffer.reset_for_tests()
    conversation = create_conversation(session, ConversationCreate(title="活跃 turn 列表"))

    completed_turn = ChatTurn(
        conversation_id=conversation.id,
        thread_id=f"conversation:{conversation.id}",
        status="completed",
        node_statuses=json.dumps({"agent": "succeeded"}, ensure_ascii=False),
    )
    running_turn = ChatTurn(
        conversation_id=conversation.id,
        thread_id=f"conversation:{conversation.id}",
        status="running",
        node_statuses=json.dumps({"agent": "running"}, ensure_ascii=False),
    )
    interrupted_turn = ChatTurn(
        conversation_id=conversation.id,
        thread_id=f"conversation:{conversation.id}",
        status="interrupted",
        node_statuses=json.dumps({"tools": "interrupted"}, ensure_ascii=False),
        debug_payload=json.dumps(
            {
                "pending_interrupt": {
                    "kind": "user_input",
                    "request_id": "choice-1",
                    "question": "请选择",
                    "options": [],
                }
            },
            ensure_ascii=False,
        ),
    )
    session.add(completed_turn)
    session.add(running_turn)
    session.add(interrupted_turn)
    session.commit()

    result = list_active_chat_turns(session, conversation_id=conversation.id)
    assert [item.turn_id for item in result.items] == [running_turn.id, interrupted_turn.id]
    assert result.items[0].status == "running"
    assert result.items[0].node_statuses == {"agent": "running"}
    assert result.items[1].status == "interrupted"
    assert result.items[1].pending_interrupt == {
        "kind": "user_input",
        "request_id": "choice-1",
        "question": "请选择",
        "options": [],
    }


def _wait_until(predicate, *, timeout: float, interval: float = 0.05) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        if predicate():
            return
        time.sleep(interval)
    raise AssertionError(f"predicate did not become true within {timeout}s")


def _parse_sse(raw_event: str) -> dict:
    event = ""
    data = "{}"
    for line in raw_event.strip().splitlines():
        if line.startswith("event:"):
            event = line.removeprefix("event:").strip()
        if line.startswith("data:"):
            data = line.removeprefix("data:").strip()
    return {"event": event, "data": json.loads(data)}
