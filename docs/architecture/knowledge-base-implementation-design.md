# Memo 知库第一版工程设计

本文是 [Memo 知库模块设计](./knowledge-base-module.md) 的落地设计，用于指导第一版实现。

第一版目标不是做完整 Dify / RAGFlow 平台，而是在 AiMemo 现有架构里增加一个可用、可控、可恢复的知库模块：

```text
知库页面
  创建知识空间、上传文档、查看处理状态、预览 chunk、搜索资料。

后台 ingest graph
  解析文档、分块、embedding、写入 sqlite-vec 和关键词索引。

对话挂载
  用户手动把知识空间挂载到当前 conversation。

聊天 RAG
  dispatch_context_workers 做首轮知库召回。
  tool loop 的 knowledge_search 只做补召回。
```

## 第一版范围

### 必做

```text
1. 数据模型：KnowledgeSpace / KnowledgeDocument / KnowledgeChunk / ConversationKnowledgeMount。
2. 向量表：vec_knowledge_chunks。
3. API：空间 CRUD、文档上传/列表/详情/chunks、知库搜索、conversation mount。
4. 后台任务：knowledge_ingest_graph。
5. 文档解析：TXT / Markdown / DOCX / PPTX / PDF。
6. 分块策略：标题/段落优先 + token 上限。
7. 搜索：向量召回 + 关键词召回的轻量 hybrid。
8. 前端：/app/knowledge 页面。
9. 对话页：当前 conversation 的知库挂载入口。
10. Agent：build_l3_knowledge_context + knowledge_search 工具。
```

### 暂不做

```text
网页 URL 抓取
文件夹同步
OCR
复杂表格结构恢复
高级 reranker
多向量库切换
自动挂载
多用户权限
chunk 人工编辑
```

## 后端文件结构

建议新增：

```text
backend/app/models/knowledge.py
backend/app/schemas/knowledge.py
backend/app/api/knowledge.py
backend/app/services/knowledge_space_service.py
backend/app/services/knowledge_document_service.py
backend/app/services/knowledge_ingest_service.py
backend/app/services/knowledge_search_service.py
backend/app/rag/knowledge_vector_store.py
backend/app/rag/knowledge_search.py
backend/app/rag/document_parsers/
  __init__.py
  base.py
  text_parser.py
  markdown_parser.py
  docx_parser.py
  pptx_parser.py
  pdf_parser.py
backend/app/rag/knowledge_chunking/
  __init__.py
  chunker.py
  config.py
backend/app/agent/graphs/knowledge_ingest/
  __init__.py
  graph.py
  nodes.py
  state.py
backend/tests/test_knowledge_*.py
```

后续接入聊天 graph 时再改：

```text
backend/app/agent/graphs/memory_chat/state.py
backend/app/agent/graphs/memory_chat/nodes.py
backend/app/agent/graphs/memory_chat/graph.py
backend/app/services/chat_service.py
```

## 数据库设计

### KnowledgeSpace

表名建议：

```text
knowledgespace
```

字段：

```text
id: int primary key
name: str
description: str
icon: str | null
status: active | archived
document_count: int
ready_document_count: int
created_at: datetime
updated_at: datetime
```

说明：

```text
document_count / ready_document_count 可第一版实时计算，也可冗余维护。
status=archived 时不允许被新挂载，不参与检索。
```

### KnowledgeDocument

表名：

```text
knowledgedocument
```

字段：

```text
id: int primary key
space_id: int foreign key
title: str
source_type: file | text | url | folder
source_uri: str | null
storage_path: str | null
original_filename: str | null
mime_type: str | null
content_hash: str
parser: str | null
chunk_strategy: str
status: pending | parsing | chunking | embedding | indexing | ready | failed | deleted
chunk_count: int
token_count: int
error_code: str | null
error_message: str | null
created_at: datetime
updated_at: datetime
processed_at: datetime | null
```

存储策略：

```text
上传文件保存到 backend/data/knowledge/files/{space_id}/{document_id}/original.ext。
数据库保存 storage_path 相对路径。
删除文档第一版可以软删 status=deleted，并从检索中过滤。
```

### KnowledgeChunk

表名：

```text
knowledgechunk
```

字段：

```text
id: int primary key
space_id: int foreign key
document_id: int foreign key
chunk_index: int
text: str
summary: str | null
heading_path: str | null      # JSON string
page_number: int | null
source_offset: int | null
token_count: int
content_hash: str
embedding_status: pending | completed | failed
embedding_error: str | null
metadata_json: str | null
created_at: datetime
updated_at: datetime
```

索引：

```text
(space_id, document_id)
(document_id, chunk_index)
content_hash
```

### vec_knowledge_chunks

`sqlite-vec` 虚拟表：

```text
vec_knowledge_chunks
  rowid = knowledgechunk.id
  embedding float[settings.embedding_dimensions]
```

和现有 `vec_note_chunks` 保持同一约定：业务表保存可读元数据，vec 表只保存向量。

### KnowledgeChunkFts

第一版推荐使用 SQLite FTS5：

```text
knowledgechunk_fts
  chunk_id
  text
  title
  heading_path
```

如果迁移复杂，可以先不建 FTS5，使用 LIKE 降级；但 service 接口要先按 `keyword_recall()` 抽象，方便替换。

### ConversationKnowledgeMount

表名：

```text
conversationknowledgemount
```

字段：

```text
id: int primary key
conversation_id: int foreign key
space_id: int foreign key
created_by: user | system
scope_note: str | null
created_at: datetime
```

约束：

```text
unique(conversation_id, space_id)
space.status 必须 active 才允许挂载。
```

## API 设计

### Spaces

```http
GET /api/knowledge/spaces
POST /api/knowledge/spaces
GET /api/knowledge/spaces/{space_id}
PATCH /api/knowledge/spaces/{space_id}
DELETE /api/knowledge/spaces/{space_id}
```

`DELETE` 第一版做归档：

```text
status = archived
取消该 space 的所有 conversation mounts
不物理删除文档和 chunk
```

### Documents

```http
GET /api/knowledge/spaces/{space_id}/documents
POST /api/knowledge/spaces/{space_id}/documents/upload
GET /api/knowledge/documents/{document_id}
DELETE /api/knowledge/documents/{document_id}
POST /api/knowledge/documents/{document_id}/reindex
GET /api/knowledge/documents/{document_id}/chunks
```

上传返回：

```json
{
  "document": { "...": "..." },
  "job": {
    "id": 123,
    "graph_name": "knowledge_ingest_graph"
  }
}
```

### Search

用于知库页面内搜索：

```http
POST /api/knowledge/search
Content-Type: application/json

{
  "query": "publisher 迁移",
  "space_id": 1,
  "top_k": 8,
  "mode": "hybrid"
}
```

注意：

```text
这个 API 是用户显式在知库页面内搜索，可以指定 space_id。
Agent 工具不直接暴露任意 space_ids，而是根据 conversation_id 读取 mount scope。
```

### Conversation Mounts

```http
GET /api/conversations/{conversation_id}/knowledge-mounts
PUT /api/conversations/{conversation_id}/knowledge-mounts
POST /api/conversations/{conversation_id}/knowledge-mounts/{space_id}
DELETE /api/conversations/{conversation_id}/knowledge-mounts/{space_id}
```

`PUT` 示例：

```json
{
  "space_ids": [1, 2]
}
```

返回当前挂载详情：

```json
[
  {
    "space_id": 1,
    "space_name": "Zenoh 项目资料",
    "ready_document_count": 4,
    "document_count": 5
  }
]
```

## knowledge_ingest_graph 设计

### State

```python
class KnowledgeIngestState(TypedDict):
    job_id: int
    document_id: int
    space_id: int
    storage_path: str
    mime_type: str | None
    parser: str | None
    blocks: list[DocumentBlock]
    chunks: list[KnowledgeChunkDraft]
    error_code: str | None
    error_message: str | None
```

### Nodes

```text
load_document
  从 DB 读取 document，校验 status、文件存在、space active。

parse_document
  根据 mime_type / 扩展名选择 parser，输出 DocumentBlock[]。
  status -> parsing。

normalize_blocks
  清理空白、合并异常断行、保留 page_number / heading_path。

chunk_document
  生成 chunk draft，计算 content_hash 和 token_count。
  status -> chunking。

persist_chunks
  删除旧 chunk/vector/fts，插入新 chunks。

embed_chunks
  调用现有 embedding provider，批量写 vec_knowledge_chunks。
  status -> embedding。

index_keywords
  写入 FTS / keyword index。
  status -> indexing。

mark_ready
  更新 document ready、processed_at、chunk_count。

mark_failed
  保存 error_code / error_message。
```

### Job 接入

新增 job：

```text
type: knowledge_ingest
graph_name: knowledge_ingest_graph
payload: {"document_id": 1}
thread_id: job:{job_id}
dedupe_key: knowledge_ingest:document:{document_id}
```

worker handler：

```text
job.graph_name == "knowledge_ingest_graph"
  -> run_knowledge_ingest_graph(job)
```

reconciler：

```text
KnowledgeDocument.status in pending/parsing/chunking/embedding/indexing
  且不存在活跃 knowledge_ingest job
  -> 补建 job
```

## Search Service 设计

### 页面搜索

```python
search_knowledge(
    query: str,
    space_ids: list[int],
    top_k: int = 8,
    mode: Literal["auto", "vector", "keyword", "hybrid"] = "hybrid",
) -> KnowledgeSearchResult
```

### Agent 搜索

```python
search_mounted_knowledge(
    conversation_id: int,
    query: str,
    top_k: int = 5,
    mode: str = "hybrid",
) -> KnowledgeSearchResult
```

必须：

```text
从 ConversationKnowledgeMount 读取 space_ids。
没有挂载时返回 NEED_KNOWLEDGE_MOUNT。
不允许调用方传入任意 space_ids。
过滤 archived space、deleted document、非 ready document。
```

### Hybrid 策略

第一版：

```text
vector_recall top 12
keyword_recall top 12
RRF 或简单加权融合
每个 document 最多保留 3 个 chunk
最终 top_k 默认 5
```

返回结果包含：

```text
chunk_id
space_id / space_name
document_id / document_title
text
score
score_source: vector | keyword | hybrid
heading_path
page_number
source_uri / original_filename
retrieval_phase
```

## Memory Chat Graph 接入

### State 扩展

建议新增：

```text
mounted_knowledge_spaces
knowledge_retrieval_plan
l3_knowledge_context
knowledge_citations
knowledge_retrieval_debug
```

### dispatch_context_workers

新增 worker：

```text
build_l3_knowledge_context
```

职责：

```text
1. 读取 conversation mounts。
2. 判断用户初问是否需要知库。
3. 无 mount 时返回 skipped/no_scope。
4. 需要时调用 search_mounted_knowledge。
5. 将结果打包为 L3_knowledge_context。
6. 保存 debug 信息供 ChatGraphPanel 展示。
```

### tool loop

新增工具：

```text
knowledge_search
```

定位：

```text
补召回，不是主路径。
首轮上下文构建已经查过但不够时使用。
仍然只能查 mounted spaces。
```

### Prompt 约束

系统提示必须包含：

```text
知库默认不进入对话。
只有当前 conversation 已挂载的知识空间可以检索。
如果没有挂载，不要声称查过知库。
使用知库内容回答时必须给出引用。
knowledge_search 是补检索工具，不应替代首轮 L3_knowledge_context。
```

## 前端设计

### 路由

```text
/app/knowledge
```

导航：

```text
Ai记 / 对话 / 知库 / 工坊
```

### 页面布局

第一版推荐三栏：

```text
左栏：知识空间
  空间列表
  新建空间
  归档空间

中栏：文档列表
  上传文档
  搜索当前空间
  文档状态
  chunk 数量
  处理失败提示

右栏：文档详情
  元数据
  处理状态
  chunk 预览
  重新处理
  删除 / 归档
```

### 对话挂载入口

ChatWindow 增加一个当前对话的知库挂载控件：

```text
当前知库：未挂载
[挂载知库]
```

挂载后：

```text
当前知库：Zenoh 项目资料、Python 文档
[管理]
```

交互：

```text
点击管理 -> 弹出或侧栏选择知识空间。
保存后调用 PUT /api/conversations/{id}/knowledge-mounts。
挂载状态应跟 conversation 切换同步刷新。
```

Agent 要查未挂载资料时，前端可以把后端的 `NEED_KNOWLEDGE_MOUNT` 渲染为可操作提示，但第一版也可以先显示普通文本。

## 测试计划

### 后端单元测试

```text
test_create_knowledge_space
test_upload_document_creates_job
test_ingest_text_document_creates_chunks_and_vectors
test_delete_document_excludes_from_search
test_search_filters_archived_space
test_search_mounted_knowledge_requires_mount
test_search_mounted_knowledge_only_searches_mounted_spaces
test_conversation_mount_crud
```

### Graph 测试

```text
test_knowledge_ingest_graph_marks_ready
test_knowledge_ingest_graph_marks_failed_on_parse_error
test_memory_chat_build_l3_knowledge_context_skips_without_mount
test_memory_chat_build_l3_knowledge_context_searches_mounted_space
```

### 前端验证

```text
npm run build
```

手动：

```text
创建知识空间
上传 TXT/Markdown
看到 job 处理完成
搜索命中文档
对话挂载知识空间
提问后回答带引用
取消挂载后同样问题不再检索
```

## 实施顺序

建议分 6 步：

```text
Step 1: 数据模型和 API skeleton
  KnowledgeSpace / KnowledgeDocument / KnowledgeChunk / ConversationKnowledgeMount
  spaces/documents/mounts 基础接口

Step 2: 文档存储、parser 和 chunker
  TXT/Markdown/DOCX/PDF parser
  chunk draft 生成

Step 3: knowledge_ingest_graph
  job 接入、chunk 入库、embedding、vec_knowledge_chunks

Step 4: search service
  vector recall、keyword recall、hybrid merge
  页面搜索 API

Step 5: 前端 /app/knowledge
  空间、上传、文档列表、chunk 预览、搜索

Step 6: 对话挂载和聊天 graph 接入
  mount UI
  build_l3_knowledge_context
  knowledge_search tool
  引用展示
```

原因：

```text
先把知库自己的 ingest/search 闭环跑通。
再接入对话挂载和 Agent，降低调试复杂度。
```

## 关键工程约束

```text
不要复用 note_chunk 表存知库 chunk。
不要让 knowledge_search 接收 LLM 任意传入的 space_ids。
不要在文档未 ready 时参与检索。
不要因为用户提到文档名自动挂载知识空间。
不要把无引用的知库回答伪装成确定事实。
```

## 第一版成功标准

```text
用户能创建“知库”空间。
用户能上传文档并看到处理状态。
文档处理失败时能看到失败原因。
用户能在知库页面搜索命中文档片段。
用户能把知识空间挂载到当前对话。
Agent 只能检索已挂载空间。
回答能展示引用来源。
Chat Graph 能显示首轮知库检索是否触发、检索范围和引用结果。
```
