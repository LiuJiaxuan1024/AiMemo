# 笔记 API

## 健康检查

```text
GET /api/health
```

响应示例：

```json
{
  "status": "ok"
}
```

## 创建笔记

```text
POST /api/notes
```

请求示例：

```json
{
  "title": "测试笔记",
  "content": "这是第一条 Ai 记测试笔记。",
  "summary": "",
  "tags": []
}
```

响应：`201 Created`

创建成功后，后端会立即返回笔记，并创建后台任务生成标题、摘要和标签。模型处理状态通过 `processing_status` 返回。

常见状态：

```text
pending
processing
completed
failed
```

## 获取笔记列表

```text
GET /api/notes
```

按 `updated_at` 倒序返回。

## 获取笔记详情

```text
GET /api/notes/{note_id}
```

不存在时返回 `404`。
