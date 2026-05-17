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

创建成功后，后端会立即返回笔记，并创建后台任务生成标题、摘要和标签，同时创建 embedding 任务。
模型处理状态通过 `processing_status` 返回，向量化状态通过 `embedding_status` 返回。

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

默认只返回生效笔记：

```text
GET /api/notes?status=active
```

最近删除列表：

```text
GET /api/notes?status=deleted
```

按 `updated_at` 倒序返回。

## 获取笔记详情

```text
GET /api/notes/{note_id}
```

不存在时返回 `404`。

## 修改笔记

```text
PATCH /api/notes/{note_id}
```

请求示例：

```json
{
  "title": "新的标题",
  "content": "新的正文内容"
}
```

规则：

```text
只能修改 active 笔记。
content 不能为空。
content 变化后会重新计算 content_hash。
content 变化后会清空旧 summary/tags，重置 processing_status 和 embedding_status。
content 变化后会清理旧 notechunk/vector，并创建新的 metadata/embedding jobs。
```

每个 job 会携带当前 `content_hash`：

```json
{
  "note_id": 1,
  "content_hash": "sha256..."
}
```

graph 执行时会再次检查：

```text
note.status == active
job.payload.content_hash == note.content_hash
```

不满足时直接跳过，避免旧任务覆盖新内容。

## 删除到最近删除

```text
DELETE /api/notes/{note_id}
```

行为：

```text
note.status = deleted
note.deleted_at = now
```

不会物理删除 note，也不会立即删除 chunks/vector。RAG 检索必须过滤
`note.status = active`，因此 deleted 笔记不会被 AI 检索使用。

## 恢复笔记

```text
POST /api/notes/{note_id}/restore
```

行为：

```text
note.status = active
note.deleted_at = null
```

如果 chunks 仍存在，恢复后可以立即重新参与检索。如果 chunks 缺失，会补建 embedding job。

## 永久删除

```text
DELETE /api/notes/{note_id}/hard
```

只允许永久删除 `status=deleted` 的笔记。

行为：

```text
删除 notechunk
删除 sqlite-vec 中对应向量
删除 note
```

响应：

```text
204 No Content
```
