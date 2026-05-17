# Memories API

长期记忆 API 用于管理 `longtermmemory` 表中的 L4 核心长期记忆。

## GET /api/memories

列出长期记忆。

### Query 参数

```text
status: active | archived
category: preference | identity | goal | instruction | event | fact
level: int
limit: int
offset: int
```

默认：

```text
status = active
level = 4
limit = 50
offset = 0
```

### Response

```json
[
  {
    "id": 1,
    "level": 4,
    "category": "preference",
    "content": "用户不吃香菜。",
    "summary": "不吃香菜",
    "importance": 0.95,
    "confidence": 0.9,
    "source_type": "chat_message",
    "source_id": 12,
    "status": "active",
    "content_hash": "sha256...",
    "created_at": "2026-05-17T12:00:00Z",
    "updated_at": "2026-05-17T12:00:00Z"
  }
]
```

## PATCH /api/memories/{memory_id}

编辑长期记忆。

### Request

所有字段均可选，但至少应提供一个字段。

```json
{
  "category": "preference",
  "content": "用户不吃香菜，也不喜欢葱。",
  "summary": "不吃香菜和葱",
  "importance": 0.9,
  "confidence": 0.95,
  "status": "active"
}
```

### 规则

```text
content 不能为空。
category 必须是允许值。
status 必须是 active 或 archived。
importance/confidence 必须在 0.0-1.0。
修改 content 或 category 后重新计算 content_hash。
```

### Response

返回更新后的记忆：

```json
{
  "id": 1,
  "level": 4,
  "category": "preference",
  "content": "用户不吃香菜，也不喜欢葱。",
  "summary": "不吃香菜和葱",
  "importance": 0.9,
  "confidence": 0.95,
  "source_type": "chat_message",
  "source_id": 12,
  "status": "active",
  "content_hash": "new-sha256...",
  "created_at": "2026-05-17T12:00:00Z",
  "updated_at": "2026-05-17T12:10:00Z"
}
```

## DELETE /api/memories/{memory_id}

停用长期记忆。

行为：

```text
status = archived
```

不物理删除记录。如需重新启用，调用 `PATCH /api/memories/{memory_id}` 并传入 `{"status":"active"}`。

### Response

返回停用后的记忆：

```json
{
  "id": 1,
  "status": "archived"
}
```

实际实现可以返回完整 `MemoryRead`，方便前端更新本地列表。

## DELETE /api/memories/{memory_id}/hard

永久删除已停用的长期记忆。

规则：

```text
只允许删除 status = archived 的记忆。
active 记忆必须先调用 DELETE /api/memories/{memory_id} 停用。
删除后无法通过 PATCH 重新启用。
```

### Response

```text
204 No Content
```

## GET /api/memories/{memory_id}

读取单条长期记忆。

### Response

返回完整 `MemoryRead`。

## GET /api/memories/{memory_id}/detail

读取长期记忆详情，并解析来源消息。

列表接口只返回记忆本体；详情接口用于调试和排查，因此会额外尝试读取
`source_type/source_id` 对应的来源记录。

### Response

```json
{
  "id": 1,
  "level": 4,
  "category": "preference",
  "content": "用户不吃香菜。",
  "summary": "不吃香菜",
  "importance": 0.95,
  "confidence": 0.9,
  "source_type": "chat_message",
  "source_id": 12,
  "status": "active",
  "content_hash": "sha256...",
  "created_at": "2026-05-17T12:00:00Z",
  "updated_at": "2026-05-17T12:00:00Z",
  "source_message": {
    "id": 12,
    "conversation_id": 2,
    "conversation_title": "记忆对话",
    "role": "assistant",
    "content": "用户说自己不吃香菜。",
    "created_at": "2026-05-17T11:59:00Z"
  }
}
```

如果来源消息已经不存在，或来源不是当前支持的 `chat_message` 类型：

```json
{
  "source_message": null
}
```

## 错误

```text
404
  memory_id 不存在。

422
  参数类型错误，例如 importance 不是数字。

400
  content 为空、category/status 不合法、importance/confidence 超出范围。
  尝试永久删除 active 记忆。
```
