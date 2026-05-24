from dataclasses import dataclass

from app.agent.streaming.mapper import map_langgraph_stream_chunk


@dataclass
class FakeTokenChunk:
    content: object


def test_maps_updates_to_node_events() -> None:
    events = map_langgraph_stream_chunk(
        "updates",
        {
            "load_turn_state": {"conversation_id": 1},
            "build_l0_current_input": {"context_l0_layer": {"content": "hello"}},
        },
    )

    assert events == [
        {
            "event": "node",
            "node": "load_turn_state",
            "state_update": {"conversation_id": 1},
        },
        {
            "event": "node",
            "node": "build_l0_current_input",
            "state_update": {"context_l0_layer": {"content": "hello"}},
        },
    ]


def test_maps_agent_token_to_answer_delta() -> None:
    events = map_langgraph_stream_chunk(
        "messages",
        (
            FakeTokenChunk(content="你好"),
            {"langgraph_node": "agent"},
        ),
    )

    assert events == [
        {
            "event": "answer_delta",
            "node": "agent",
            "content": "你好",
            "metadata": {"langgraph_node": "agent"},
        }
    ]


def test_maps_non_answer_llm_token_to_internal_token() -> None:
    events = map_langgraph_stream_chunk(
        "messages",
        (
            FakeTokenChunk(content='{"needs_retrieval": true}'),
            {"langgraph_node": "build_l3_retrieved_memory"},
        ),
    )

    assert events[0]["event"] == "internal_token"
    assert events[0]["node"] == "build_l3_retrieved_memory"


def test_maps_elf_bubble_answer_token_to_bubble_delta() -> None:
    events = map_langgraph_stream_chunk(
        "messages",
        (
            FakeTokenChunk(content='{"bubbles":'),
            {"langgraph_node": "generate_elf_bubble_answer"},
        ),
    )

    assert events == [
        {
            "event": "bubble_delta",
            "node": "generate_elf_bubble_answer",
            "content": '{"bubbles":',
            "metadata": {"langgraph_node": "generate_elf_bubble_answer"},
        }
    ]


def test_extracts_text_from_list_content() -> None:
    events = map_langgraph_stream_chunk(
        "messages",
        (
            FakeTokenChunk(content=[{"type": "text", "text": "你"}, {"type": "text", "text": "好"}]),
            {"langgraph_node": "agent"},
        ),
    )

    assert events[0]["event"] == "answer_delta"
    assert events[0]["content"] == "你好"
