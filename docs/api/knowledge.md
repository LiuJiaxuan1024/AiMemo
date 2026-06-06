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

文档响应会包含导入后的处理统计：

```text
chunk_count
  文档最终生成的全部 chunk 数。

text_chunk_count
  正文 / 表格等非图片来源 chunk 数。

image_asset_count
  解析阶段抽取到的图片资源数量。

image_asset_processed_count
  已成功转成图片文本 block 的图片资源数量。

image_text_chunk_count
  图片文本 block 经过 chunking 后生成的 chunk 数。

image_asset_failed_count
  图片为空、过大、模型输出低置信度、provider 未配置或解析失败等未生成图片文本的数量。
```

图片资源进入知识库后不单独建立图片检索池。后端会把通过质量过滤的图片分析结果写成 `[图片文本]` block，再进入统一 chunk / embedding 流程；对话检索阶段只看到文本 chunk 和来源 metadata。

`DELETE /documents/{document_id}` 会把文档软删除，并清理对应 chunks、向量索引和上传文件。

## 图片转文本状态

```http
GET /api/knowledge/ocr/status
POST /api/knowledge/ocr/install
Content-Type: application/json

{
  "confirm_install": true
}
```

历史上该接口用于本地 OCR 检测，因此路径仍保留 `/ocr/*`。当前默认配置下，知识库图片转文本使用 DashScope `qwen-vl-ocr`：

```text
mode = qwen_vl_ocr
  status 只检查 DASHSCOPE_API_KEY 是否可用。
  ready=true 表示可以调用 qwen-vl-ocr。
  ready=false 且 status=provider_not_configured 表示缺少 DASHSCOPE_API_KEY。
  install 接口不会安装本地 OCR，会返回无需安装 / provider 未配置。

mode = local_ocr
  status 检测 tesseract 命令、语言包、托管 tessdata 和 Python OCR 包。
  install 接口才会创建本地 OCR / 语言包安装后台任务。
```

默认 qwen 模式不会在缺 Key 或模型失败时自动回退到本地 Tesseract，避免把本地 OCR 噪声写入索引。

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
