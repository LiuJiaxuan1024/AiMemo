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

## 当前限制

- 不生成 AI 回复。
- 不执行 `memory_chat_graph`。
- 不支持编辑消息。
- 不支持分支切换 UI。

这些能力会在后续 graph 和前端阶段实现。

