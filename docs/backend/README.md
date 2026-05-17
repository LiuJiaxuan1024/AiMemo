# 后端说明

后端位于 `backend/`，使用 FastAPI 提供 HTTP API，使用 SQLModel 访问 SQLite。

## 目录说明

```text
backend/app/
  api/       HTTP 路由
  agent/     LangGraph 相关代码
  core/      配置、数据库等基础设施
  models/    数据库模型
  rag/       chunk、hash、向量存储等 RAG 基础能力
  schemas/   API 入参和出参模型
  services/  业务逻辑
  main.py    FastAPI 应用入口
```

## 关键文件

- `app/main.py`: 创建 FastAPI 应用、注册 CORS 和路由、启动时创建数据库表。
- `app/core/config.py`: 读取 `.env` 和默认配置。
- `app/core/database.py`: 创建 SQLModel engine，提供 session 依赖。
- `app/models/note.py`: 定义 `Note` 数据模型。
- `app/models/note_chunk.py`: 定义笔记 chunk 数据模型。
- `app/rag/chunking/`: 定义笔记分片策略。
- `app/rag/vector_store.py`: 管理 `sqlite-vec` 向量表。
- `app/schemas/note.py`: 定义笔记 API 的请求和响应结构。
- `app/services/note_service.py`: 封装笔记创建、列表和详情读取逻辑。
- `app/api/notes.py`: 暴露笔记相关 API。
- `app/api/health.py`: 暴露健康检查 API。

## 数据库

默认数据库地址：

```text
backend/data/ai_note.db
```

该文件属于本地运行时数据，不进入版本管理。

## 当前 Note 模型

```text
notes
  id
  title
  content
  summary
  tags
  processing_status
  embedding_status
  embedding_error
  embedded_at
  created_at
  updated_at
```

当前 `tags` 暂以逗号分隔字符串保存。后续如果需要更强的标签查询能力，应拆分为 `tags` 和 `note_tags` 两张表。

## 相关文档

- [笔记智能处理](./note-processing.md)
- [本地任务系统](./jobs.md)
- [对话持久化](./conversations.md)
- [长期记忆管理](./memories.md)
- [向量存储](./vector-storage.md)
- [向量检索](./vector-search.md)
