from app.agent.context import (
    ContextBudget,
    build_adjacent_turn_layer,
    build_current_conversation_window_layer,
    build_memory_chat_prompt_context,
)


def test_pyramid_context_includes_summary_and_current_input():
    context = build_memory_chat_prompt_context(
        user_message="我之前说过想吃什么？",
        recent_messages=[],
        conversation_summary="用户最近在规划午餐。",
        retrieved_chunks=[],
        needs_retrieval=True,
        retrieval_grade="none",
    )

    prompt = context.to_prompt()
    assert "L2 对话摘要" in prompt
    assert "用户最近在规划午餐。" in prompt
    assert "L1 近期对话窗口" in prompt
    assert "L0 当前用户输入" in prompt
    assert "我之前说过想吃什么？" in prompt


def test_pyramid_context_keeps_recent_messages_within_budget():
    context = build_memory_chat_prompt_context(
        user_message="继续说",
        recent_messages=[
            {"role": "user", "content": "很早以前的消息", "token_count": 50},
            {"role": "assistant", "content": "中间消息", "token_count": 50},
            {"role": "user", "content": "最近消息", "token_count": 1},
        ],
        conversation_summary="",
        retrieved_chunks=[],
        needs_retrieval=False,
        retrieval_grade="none",
        budget=ContextBudget(recent_message_tokens=14),
    )

    prompt = context.to_prompt()
    assert "user: 最近消息" in prompt
    assert "L0 当前用户输入" in prompt
    assert "继续说" in prompt
    assert "很早以前的消息" not in prompt


def test_current_conversation_window_merges_recent_messages_and_current_input():
    layer = build_current_conversation_window_layer(
        [
            {"role": "assistant", "content": "我建议保存到 E:\\test\\message.txt", "token_count": 10},
        ],
        "直接保存到这个文件",
        ContextBudget(),
    )

    assert layer.level == 1
    assert layer.name == "当前对话窗口（L0+L1 合并）"
    assert layer.kind == "fused"
    assert "assistant: 我建议保存到 E:\\test\\message.txt" in layer.content
    assert "user(current): 直接保存到这个文件" in layer.content


def test_adjacent_turn_layer_prioritizes_latest_turn_for_ambiguous_followup():
    layer = build_adjacent_turn_layer(
        [
            {"role": "user", "content": "帮我写好友推荐的 PySpark 代码", "token_count": 10},
            {"role": "assistant", "content": "这里是好友推荐的完整代码", "token_count": 10},
            {"role": "user", "content": "Dataset 过滤年龄不等于 23 应该怎么写？", "token_count": 10},
            {
                "role": "assistant",
                "content": "上一轮我们在改 SparkSQL Dataset notEqual(23) 的 Test2 代码。",
                "token_count": 10,
            },
        ],
        "提供完整代码吗",
        ContextBudget(adjacent_message_tokens=80),
    )

    assert layer.level == 0.5
    assert layer.name == "最近一轮邻接上下文"
    assert "notEqual(23)" in layer.content
    assert "user(current): 提供完整代码吗" in layer.content
    assert "好友推荐" not in layer.content


def test_pyramid_context_places_adjacent_layer_before_current_input():
    context = build_memory_chat_prompt_context(
        user_message="提供完整代码吗",
        recent_messages=[
            {
                "role": "assistant",
                "content": "上一轮我们在改 SparkSQL Dataset notEqual(23) 的 Test2 代码。",
                "token_count": 10,
            },
        ],
        conversation_summary="历史背景：用户之前也问过好友推荐代码。",
        retrieved_chunks=[],
        needs_retrieval=False,
        retrieval_grade="none",
    )

    prompt = context.to_prompt()
    adjacent_index = prompt.index("L0.5 最近一轮邻接上下文")
    current_index = prompt.index("L0 当前用户输入")
    assert adjacent_index < current_index
    assert "notEqual(23)" in prompt


def test_pyramid_context_marks_weak_retrieval_as_uncertain_and_limits_chunks():
    chunks = [
        {
            "note_title": f"笔记 {index}",
            "content": f"内容 {index}",
            "score": 0.45,
        }
        for index in range(5)
    ]
    context = build_memory_chat_prompt_context(
        user_message="那个是什么？",
        recent_messages=[],
        conversation_summary="",
        retrieved_chunks=chunks,
        needs_retrieval=True,
        retrieval_grade="weak",
        budget=ContextBudget(weak_retrieval_max_chunks=2),
    )

    prompt = context.to_prompt()
    assert "可能相关但不确定" in prompt
    assert "笔记 0" in prompt
    assert "笔记 1" in prompt
    assert "笔记 2" not in prompt


def test_pyramid_context_hides_poor_retrieval_chunks():
    context = build_memory_chat_prompt_context(
        user_message="我之前提过什么？",
        recent_messages=[],
        conversation_summary="",
        retrieved_chunks=[
            {
                "note_title": "弱相关笔记",
                "content": "这段内容不应该进入 prompt",
                "score": 0.1,
            }
        ],
        needs_retrieval=True,
        retrieval_grade="poor",
    )

    prompt = context.to_prompt()
    assert "没有检索到足够可靠的笔记" in prompt
    assert "这段内容不应该进入 prompt" not in prompt
