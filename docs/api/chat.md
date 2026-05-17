# Chat API

Chat API 是 `memory_chat_graph` 的 HTTP 入口。它会执行一轮记忆对话：读取上下文、判断是否检索、生成回答、保存用户消息和 AI 消息。

## 发送消息

```http
POST /api/conversations/{conversation_id}/chat
Content-Type: application/json

{
  "message": "我之前说过想吃什么？"
}
```

## 响应

```json
{
  "conversation_id": 1,
  "thread_id": "conversation:1",
  "checkpoint_id": "...",
  "needs_retrieval": true,
  "needs_query_rewrite": false,
  "retrieval_query": "我之前说过想吃什么？",
  "retrieval_grade": "good",
  "retrieval_grade_reason": "最高相似度分数 0.516 达到 good 阈值。",
  "retrieval_reason": "用户问题包含个人记忆查询线索。",
  "user_message": {
    "id": 1,
    "conversation_id": 1,
    "role": "user",
    "content": "我之前说过想吃什么？",
    "parent_id": null,
    "checkpoint_id": "...",
    "status": "completed",
    "token_count": 13,
    "created_at": "...",
    "updated_at": "..."
  },
  "assistant_message": {
    "id": 2,
    "conversation_id": 1,
    "role": "assistant",
    "content": "你之前说过中午想吃炸鸡。",
    "parent_id": 1,
    "checkpoint_id": "...",
    "status": "completed",
    "token_count": 14,
    "created_at": "...",
    "updated_at": "..."
  },
  "retrieved_chunks": []
}
```

## 当前行为

- 使用 `conversation:{conversation_id}` 作为 LangGraph `thread_id`。
- 先读取最近消息作为上下文。
- 通过规则判断是否需要检索笔记。
- 规则不确定时，使用 LLM 结构化规划是否检索和是否改写 query。
- 需要检索时调用 `search_notes(retrieval_query, limit=5)`。
- 检索后使用轻量评分得到 `good / weak / poor / none`。
- 生成回答后，用户消息和 AI 消息会一起保存到 `chatmessage`。
- graph 完成后，会把最终 checkpoint_id 回写到两条消息。

## 流式发送消息

```http
POST /api/conversations/{conversation_id}/chat/stream
Content-Type: application/json
Accept: text/event-stream

{
  "message": "我之前提到过什么计划？"
}
```

该接口返回 SSE。第一版事件包括：

```text
turn
  创建本轮 ChatTurn，返回 turn_id 和初始 node_statuses。

node
  某个 LangGraph 节点完成，返回节点名和当前 node_statuses。

answer_delta
  回答增量。来自 LangGraph stream_mode="messages" 的 LLM token。
  后端只转发 generate_answer 节点的 token，内部 planner 等 LLM token 默认不暴露。

done
  本轮完成，返回 turn_id 和完整 ChatResponse。

error
  本轮失败，返回错误信息和已知节点状态。
```

示例：

```text
event: node
data: {"node":"build_l3_retrieved_memory","node_statuses":{"build_l3_retrieved_memory":"succeeded"}}

event: answer_delta
data: {"content":"我查到你之前提到..."}
```

## 消息 Graph 调试

```http
GET /api/conversations/{conversation_id}/messages/{message_id}/graph
```

`message_id` 应为 assistant 消息 ID。响应包含：

```text
turn_id
thread_id
checkpoint_id
node_statuses
mermaid
context_layers
retrieved_chunks
```

用途：

- 前端点击某条 AI 回复右侧的 `图` 按钮后，读取本轮 Memory Chat Graph。
- Mermaid 图结构来自 LangGraph 原生 `draw_mermaid()`。
- 节点颜色来自 ChatTurn 记录的 `node_statuses`。
- `context_layers` 用于查看本轮 L0-L4 金字塔上下文。
- `retrieved_chunks` 用于排查 L3 RAG 检索质量。

## 当前限制

- 不做多 query rewrite。
- 不做 LLM retrieval grading。
- 不做消息编辑和 checkpoint 分支 UI。

## LangGraph Stream 映射

后端底层使用：

```python
app.stream(..., stream_mode=["updates", "messages"])
```

映射规则：

```text
updates
  -> node
  用于更新 ChatTurn.node_statuses 和前端 graph 高亮。

messages + metadata.langgraph_node == "generate_answer"
  -> answer_delta
  用户可见回答 token。

messages + metadata.langgraph_node != "generate_answer"
  -> internal_token
  默认不发给前端，避免暴露 planner JSON 等内部 LLM 输出。

graph final snapshot
  -> done
  读取最终 state，保存 ChatTurn 调试数据，并返回完整 ChatResponse。
```

后端不会把 LangGraph 原始 chunk 原样发给前端。原因是 LangGraph 事件结构面向 Python
运行时，前端需要稳定的 Ai 记 SSE 协议，同时我们还需要过滤内部 LLM token、维护
ChatTurn 状态并在 graph 完成后做业务收尾。
