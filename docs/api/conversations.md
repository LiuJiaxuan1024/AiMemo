# Conversations API

Conversations API 管理业务层对话和消息。当前只做持久化，不触发 AI 回复。

## 创建对话

```http
POST /api/conversations
Content-Type: application/json

{
  "title": "记忆问答"
}
```

响应：

```json
{
  "id": 1,
  "title": "记忆问答",
  "status": "active",
  "summary": "",
  "summary_message_id": null,
  "langgraph_thread_id": "conversation:1",
  "created_at": "...",
  "updated_at": "..."
}
```

`langgraph_thread_id` 约定为 `conversation:{id}`。后续 `memory_chat_graph` 会使用它绑定 LangGraph checkpoint。

## 获取对话列表

```http
GET /api/conversations
```

按 `updated_at` 倒序返回。

## 获取对话详情

```http
GET /api/conversations/{conversation_id}
```

## 获取消息列表

```http
GET /api/conversations/{conversation_id}/messages
```

MVP 阶段按创建时间顺序返回线性消息。后续如果实现对话状态树，前端可以基于 `parent_id` 组装树。

## 追加消息

```http
POST /api/conversations/{conversation_id}/messages
Content-Type: application/json

{
  "role": "user",
  "content": "我之前说过想吃什么？",
  "parent_id": null,
  "checkpoint_id": null,
  "status": "completed"
}
```

字段说明：

- `role`: 当前支持 `user / assistant / system`。
- `parent_id`: 可选。为空时后端默认接在当前会话最后一条消息后。
- `checkpoint_id`: 可选。MVP 阶段通常为空，后续由 `memory_chat_graph` 写入。
- `status`: 默认 `completed`。

响应：

```json
{
  "id": 1,
  "conversation_id": 1,
  "role": "user",
  "content": "我之前说过想吃什么？",
  "parent_id": null,
  "checkpoint_id": null,
  "status": "completed",
  "token_count": 12,
  "created_at": "...",
  "updated_at": "..."
}
```

## 删除对话

```http
DELETE /api/conversations/{conversation_id}
```

返回 `204 No Content`。会同步级联清理：

```text
1. 后台命令任务（BackgroundShellPool.kill + prune；OS 进程、日志、DB 行一并释放）
2. 长期记忆（LongTermMemory 中 source_type=chat_message 且 source_id 属于本对话）
3. 智能体操作审计（AgentOperation.conversation_id == id）
4. 排队中的 job（dedupe_key LIKE 'conversation_%:conversation:{id}'，覆盖 summary / memory / title）
5. ChatTurn / ChatMessage
6. LangGraph SqliteSaver checkpoint（thread_id=conversation:{id}）
7. Conversation 主表
```

对 pool / checkpoint 的清理为 best-effort，单步失败不会阻塞主流程；
数据库主表删除失败会回滚整笔事务。

## 自动命名

会话首次完成 user 消息后，后端会异步触发 [Conversation Title Graph](../agent/conversation-title-graph.md)，
让 `title` 从默认的「新对话」更新为 ≤ 16 字的中文短标题。前端在发送完一条消息后会以
1.5s / 4.5s 两次轮询 `GET /api/conversations`，让侧栏卡片自然刷新。

## 当前限制

- 不生成 AI 回复。
- 不执行 `memory_chat_graph`。
- 不支持编辑消息。
- 不支持分支切换 UI。

这些能力会在后续 graph 和前端阶段实现。

