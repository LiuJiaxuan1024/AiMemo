# 笔记智能处理

笔记智能处理负责在用户创建笔记后，通过后台 job 和 LangGraph 生成结构化元数据，并把笔记内容写入本地向量索引。

## 当前能力

创建笔记时会立即保存原始内容，并创建两个后台 job：

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
  负责创建 note 和 enqueue job，不直接调用模型。

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
  -> save note(processing_status=pending)
  -> enqueue note_metadata job
  -> enqueue note_embedding job
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

## 启动补偿

服务启动时会执行一次 job reconcile，并启动周期扫描器：

```text
backend/app/jobs/reconciler.py
```

如果历史笔记处于 `processing_status=pending/processing`，但没有活跃 `note_metadata` job，会自动补建 metadata job。

如果历史笔记处于 `embedding_status=pending/processing`，但没有活跃 `note_embedding` job，会自动补建 embedding job。

这个机制不直接执行 AI 逻辑，只负责修复“业务状态需要任务，但任务队列缺失”的不一致。真正执行仍然由 `JobWorker` 领取 job 后完成。
