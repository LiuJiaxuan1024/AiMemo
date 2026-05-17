# 笔记智能处理

笔记智能处理负责在用户创建笔记后，通过后台 job 和 LangGraph 生成结构化元数据，并把笔记内容写入本地向量索引。

## 当前能力

创建笔记时会立即保存原始内容，计算 `content_hash`，并创建两个后台 job：

```text
note_metadata
  执行 note_metadata_graph，生成标题、摘要、标签。

note_embedding
  执行 note_embedding_graph，生成 chunk 和向量索引。
```

后台 worker 会执行 `note_metadata_graph` 生成：

- 标题 `title`
- 摘要 `summary`
- 标签 `tags`

如果用户手动填写了标题，则保留用户标题。fallback 标题可被 AI 标题覆盖。

如果模型不可用、API Key 缺失或返回格式异常，笔记仍然已经保存，后台任务会进入重试或失败状态。

## 文件职责

```text
backend/app/agent/model.py
  负责创建 Agent 默认聊天模型。

backend/app/ai/prompts.py
  存放笔记元数据生成提示词。

backend/app/ai/json_utils.py
  负责从模型输出中解析 JSON 对象。

backend/app/ai/note_metadata.py
  定义 NoteMetadata 结构，并封装模型调用和结果归一化。

backend/app/services/note_service.py
  负责创建、修改、软删除、恢复、永久删除 note，并 enqueue job，不直接调用模型。

backend/app/jobs/
  管理本地持久化任务队列。

backend/app/agent/graphs/note_metadata/
  定义 note_metadata_graph 的状态、节点和执行入口。

backend/app/agent/graphs/note_embedding/
  定义 note_embedding_graph 的状态、节点和执行入口。

backend/app/rag/
  定义 chunk、content_hash 和 sqlite-vec 向量存储。
```

## 当前模型

```text
provider: 阿里云百炼 DashScope OpenAI-compatible API
chat model: qwen3.5-plus
embedding model: text-embedding-v4
embedding dimensions: 1024
api_key: DASHSCOPE_API_KEY
```

## 后续演进

当前处理链路：

```text
POST /api/notes
  -> save note(status=active, content_hash=...)
  -> enqueue note_metadata job(payload.note_id + payload.content_hash)
  -> enqueue note_embedding job(payload.note_id + payload.content_hash)
  -> worker claims job
  -> LangGraph checkpoint execution
       load_note
       generate_metadata
       write_metadata
```

embedding 链路：

```text
POST /api/notes
  -> save note(embedding_status=pending)
  -> enqueue note_embedding job
  -> worker claims job
  -> LangGraph checkpoint execution
       load_note
       split_note
       write_chunks
       generate_embeddings
       write_vector_index
       mark_embedding_completed
```

## 修改与内容版本

笔记修改不是简单覆盖。`content` 变化时会产生新的内容版本：

```text
PATCH /api/notes/{id}
  -> 更新 title/content
  -> 重新计算 note.content_hash
  -> 清空 summary/tags
  -> processing_status = pending
  -> embedding_status = pending
  -> 删除旧 notechunk 和旧 vector
  -> 创建 metadata job
  -> 创建 embedding job
```

job 的 `dedupe_key` 带当前内容 hash：

```text
note_metadata:note:{id}:content:{content_hash}
note_embedding:note:{id}:content:{content_hash}
```

graph 节点执行前也会检查：

```text
note.status == active
job.payload.content_hash == note.content_hash
```

如果用户在旧 job 执行期间修改或删除了笔记，旧 job 会跳过，不会把旧 metadata、
chunks 或 vectors 写回当前笔记。

## 最近删除

删除笔记采用软删除：

```text
DELETE /api/notes/{id}
  -> note.status = deleted
  -> note.deleted_at = now
```

软删除不会立即删除 chunks/vector。检索层通过 `note.status = active` 过滤，
保证 deleted 笔记不会被 RAG 或 Memory Chat Graph 使用。

恢复：

```text
POST /api/notes/{id}/restore
  -> note.status = active
  -> note.deleted_at = null
```

如果 chunks 还在，恢复后可以立即检索；如果 chunks 缺失，会补建 embedding job。

永久删除：

```text
DELETE /api/notes/{id}/hard
  -> delete notechunk
  -> delete sqlite-vec vectors
  -> delete note
```

## 启动补偿

服务启动时会执行一次 job reconcile，并启动周期扫描器：

```text
backend/app/jobs/reconciler.py
```

如果 active 历史笔记处于 `processing_status=pending/processing`，但没有活跃 `note_metadata` job，会自动补建 metadata job。

如果 active 历史笔记处于 `embedding_status=pending/processing`，但没有活跃 `note_embedding` job，会自动补建 embedding job。

补建逻辑兼容旧版 dedupe key：

```text
旧版: note_metadata:note:{id}
新版: note_metadata:note:{id}:content:{content_hash}
```

这个机制不直接执行 AI 逻辑，只负责修复“业务状态需要任务，但任务队列缺失”的不一致。真正执行仍然由 `JobWorker` 领取 job 后完成。
