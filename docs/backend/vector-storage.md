# 向量存储

Ai 记当前使用 SQLite 业务表保存 chunk 元数据，使用 `sqlite-vec` 虚拟表保存向量。

## 存储结构

```text
note
  id
  content
  content_hash
  status
  embedding_status
  embedding_error
  embedded_at
  deleted_at

notechunk
  id
  note_id
  chunk_index
  content
  content_hash
  token_count
  embedding_status
  embedding_error

vec_note_chunks
  rowid = notechunk.id
  embedding float[1024]
```

`notechunk` 是业务可读表，方便排查 chunk 内容、token 数、状态和错误。`vec_note_chunks` 是向量检索表，只保存向量本身。

## rowid 约定

`vec_note_chunks.rowid` 固定等于 `notechunk.id`。

这个约定让业务表和向量表之间不需要额外映射表。删除或重建 chunk 时，代码会先删除对应 rowid 的向量，再写入新的 chunk 和向量。

## 删除与检索边界

笔记删除进入最近删除：

```text
note.status = deleted
```

此时 chunks/vector 可以保留，但所有 RAG 检索必须 join note 并过滤：

```text
note.status = active
```

因此 deleted 笔记不会被 `/api/search/notes` 或 Memory Chat Graph 的 L3 检索使用。

永久删除时才会物理清理：

```text
delete notechunk rows
delete vec_note_chunks rowid
delete note row
```

## Chunk 策略

当前策略位于 `backend/app/rag/chunking/`：

```text
SHORT_NOTE_MAX_TOKENS = 512
CHUNK_TARGET_TOKENS = 384
CHUNK_MAX_TOKENS = 512
CHUNK_OVERLAP_TOKENS = 64
```

规则：

- 短笔记不拆分，整条作为一个 chunk。
- 长笔记优先按段落组合，尽量保留语义完整性。
- 单段超过上限时，退回 token 硬切。
- 相邻 chunk 保留 64 token overlap，降低语义断裂风险。

## 幂等策略

第一版向量化采用“重建式写入”：

```text
delete old vectors
delete old notechunk rows
insert new notechunk rows
generate embeddings
upsert vectors by notechunk.id
```

这种策略不追求最少写入，但恢复和排查更直接。后续如果需要支持大规模笔记或版本历史，可以基于 `content_hash` 做增量更新。

## 本地数据库路径

默认配置是：

```text
DATABASE_URL=sqlite:///./data/ai_note.db
```

目前服务从 `backend/` 目录启动，所以实际数据库文件是：

```text
backend/data/ai_note.db
```

如果从仓库根目录直接运行脚本，`./data/ai_note.db` 会指向另一个位置。排查真实服务数据时，应在 `backend/` 目录下执行脚本。

## 相关代码

```text
backend/app/models/note_chunk.py
backend/app/rag/vector_store.py
backend/app/rag/search.py
backend/app/rag/hashing.py
backend/app/rag/chunking/
backend/app/agent/graphs/note_embedding/
```

向量读取流程见 [向量检索](./vector-search.md)。
