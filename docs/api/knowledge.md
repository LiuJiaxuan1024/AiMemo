# Knowledge API

Knowledge API 管理 Memo 知库的知识空间、文档导入、chunk 预览和检索。

## 知识空间

```http
GET /api/knowledge/spaces
POST /api/knowledge/spaces
GET /api/knowledge/spaces/{space_id}
PATCH /api/knowledge/spaces/{space_id}
DELETE /api/knowledge/spaces/{space_id}
```

`DELETE` 当前是归档知识空间，不做物理删除。

## 文档

```http
GET /api/knowledge/spaces/{space_id}/documents
POST /api/knowledge/spaces/{space_id}/documents/upload
GET /api/knowledge/documents/{document_id}
DELETE /api/knowledge/documents/{document_id}
GET /api/knowledge/documents/{document_id}/chunk-drafts
GET /api/knowledge/documents/{document_id}/chunks
```

上传文档会创建 `knowledge_ingest_graph` 后台任务。第一版支持 TXT、Markdown、DOCX、PPTX 和 PDF。

`DELETE /documents/{document_id}` 会把文档软删除，并清理对应 chunks、向量索引和上传文件。

## 搜索

```http
POST /api/knowledge/search
Content-Type: application/json

{
  "query": "Zenoh publisher 迁移",
  "space_id": 1,
  "top_k": 8,
  "mode": "hybrid"
}
```

`mode` 支持：

```text
hybrid
  向量召回 + 关键词召回融合，默认推荐。

vector
  只使用 embedding 向量相似度，适合语义改写较多的问题。

keyword
  只使用关键词匹配，适合精确术语、文件名或接口名。
```

该搜索接口用于知库页面内检索，可以显式传 `space_id`。Agent 对话中的检索不直接使用全局范围，
而是走 conversation mount scope：只能搜索当前对话已挂载的知识空间。
