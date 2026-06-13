# 知识库图片明细与定向重试设计

本文设计知识库图片资源明细表，以及基于明细表的失败图片定向重试能力。

## 背景

当前知识库文档只在 `KnowledgeDocument` 上记录图片处理汇总：

```text
image_asset_count
image_asset_processed_count
image_text_chunk_count
image_asset_failed_count
```

这能让前端知道“有几张图片失败”，但不知道：

```text
哪一张图片失败
失败原因是什么
是否值得重试
已经成功的图片对应哪些 chunk
重试时应该只处理哪几张图片
```

因此当前的 `POST /api/knowledge/documents/{id}/retry-image-processing` 只能作为临时兜底：

```text
不重新上传文件，但会重新读取原始文档。
会重新解析整份文档。
会重建整份文档的 chunk 和向量索引。
会重新处理文档内所有图片。
```

对于包含大量图片的 PDF / PPTX，这会浪费时间和 qwen-vl-ocr 调用费用。目标状态应该是：

```text
只重试失败图片。
成功图片的 OCR / 视觉结果和 chunk 不动。
正文 chunk 不重建。
用户能看到每张图片的状态、失败原因和重试次数。
```

## 目标

1. 为每个文档图片资源建立 `KnowledgeImageAsset` 明细。
2. 图片处理成功、跳过、失败都写入可查询状态。
3. 支持按文档重试失败图片，也支持后续扩展为单张图片重试。
4. 定向重试只调用失败图片的图片转文本模型，不重跑成功图片。
5. 重试成功后只追加或替换对应图片 chunk，并更新向量索引。
6. 保留当前文档级重试接口作为 legacy fallback，但前端文案必须避免误导。

## 非目标

第一阶段不做图片坐标级预览、高亮回源或多模态向量库。

第一阶段不强制保存所有图片二进制。优先从已保存的原始文档重新抽取图片，再按 `asset_id` / `content_hash` 匹配失败资产。

第一阶段不要求把老文档百分百恢复成逐图明细。老文档没有完整历史，只能尽量从原始文件和已有 image chunk metadata 反推。

## 数据模型

### KnowledgeImageAsset

建议新增表：

```text
knowledge_image_assets
  id
  space_id
  document_id

  asset_id
  asset_uid
  parser
  location_label
  page_number
  source_offset
  heading_path_json
  alt_text
  caption

  mime_type
  width
  height
  bbox
  content_hash
  byte_size

  status
  retryable
  attempt_count
  extractor
  image_type
  confidence
  should_index

  error_code
  error_message
  token_usage_json
  last_attempted_at
  processed_at
  created_at
  updated_at
```

字段说明：

```text
asset_id
  parser 生成的局部 ID，例如 pdf-page-3-image-2。

asset_uid
  稳定唯一键，建议 sha256(document_id + parser + page_number + source_offset + content_hash)。
  用于同一文档内唯一标识一张图片。

content_hash
  图片二进制 sha256。用于判断重复图片、迁移匹配和 parser 版本变化后的兜底匹配。

status
  pending | processing | completed | skipped | failed | stale

retryable
  后端根据错误类型判断。网络、超时、限流、服务端错误通常可重试；
  图片为空、过大、不支持格式、低价值、低置信度通常不可自动重试。

attempt_count
  该图片累计处理次数。模型内部单次处理仍可有 max_attempts=3 的短重试；
  attempt_count 记录的是用户可见的“处理轮次”。

token_usage_json
  qwen-vl-ocr 返回的 usage，用于后续成本统计。
```

建议索引：

```text
(document_id, asset_id)
(document_id, asset_uid) unique
(document_id, status)
(document_id, retryable)
(content_hash)
```

### KnowledgeImageAssetChunk

建议增加图片资产与 chunk 的关联表，而不是把 chunk id 列表塞进 JSON：

```text
knowledge_image_asset_chunks
  id
  image_asset_id
  chunk_id
  created_at
```

用途：

```text
重试某张已成功图片时，可以删除/替换它旧的 image chunk。
前端可以从图片明细跳到对应 chunk。
清理文档时能级联清理关联关系。
```

第一阶段如果只允许重试 `failed` 图片，理论上失败图片没有旧 chunk，可以先不替换旧 chunk。
但建关联表的成本低，建议一并设计，避免后续补迁移。

## 状态语义

```text
pending
  图片已发现，尚未处理。

processing
  正在调用图片转文本。

completed
  图片已生成可检索文本，并至少关联一个 image chunk。

skipped
  图片被明确跳过，不生成 chunk。典型原因：低价值、低置信度、装饰图、格式不支持。

failed
  图片处理失败。可能可重试，也可能不可重试，取决于 retryable。

stale
  图片记录来自旧版本原文档或旧 parser，当前原始文件无法再匹配。
```

错误分类建议：

```text
DASHSCOPE_REQUEST_TIMEOUT        retryable=true
DASHSCOPE_REQUEST_FAILED         HTTP 408/409/425/429/5xx 时 retryable=true
DASHSCOPE_BAD_RESPONSE           retryable=true
MODEL_JSON_PARSE_FAILED          retryable=true
IMAGE_EMPTY                      retryable=false
IMAGE_TOO_LARGE                  retryable=false
IMAGE_MIME_UNSUPPORTED           retryable=false
IMAGE_TEXT_SKIPPED_LOW_VALUE     retryable=false
IMAGE_TEXT_LOW_CONFIDENCE        retryable=false
IMAGE_TEXT_EMPTY                 retryable=false
IMAGE_TEXT_LOW_QUALITY           retryable=false
```

## Ingest 流程改造

当前流程：

```text
parse_document_file
  -> parsed.blocks
  -> parsed.image_assets
  -> _build_image_analysis_blocks
  -> build_chunk_drafts
  -> persist chunks
  -> embeddings
```

目标流程：

```text
parse_document_file
  -> upsert KnowledgeImageAsset rows
  -> process pending/retryable image assets
  -> write image analysis blocks
  -> build_chunk_drafts
  -> persist chunks
  -> link image assets to chunks
  -> embeddings
```

关键规则：

1. 解析阶段先为每个 `DocumentImageAsset` 计算 `content_hash` 和 `asset_uid`。
2. 同一文档同一 `asset_uid` 已存在时更新位置、尺寸等 metadata，不重复创建。
3. 图片处理开始前将 asset 状态置为 `processing`，结束后置为 `completed` / `skipped` / `failed`。
4. 生成 image chunk 后写入 `KnowledgeImageAssetChunk`。
5. 文档汇总字段仍保留，由明细表聚合更新：

```text
image_asset_count = count(all assets)
image_asset_processed_count = count(completed + skipped)
image_text_chunk_count = count(image chunks)
image_asset_failed_count = count(failed)
```

## 定向重试流程

新增后台 job 类型建议：

```text
JobType.KNOWLEDGE_IMAGE_RETRY = "knowledge_image_retry"
GraphName.KNOWLEDGE_IMAGE_RETRY = "knowledge_image_retry_graph"
```

也可以第一阶段不用新 graph，只新增 service + job handler；但从可观测性看，单独 job 更清楚。

接口建议：

```text
POST /api/knowledge/documents/{document_id}/image-assets/retry-failed

POST /api/knowledge/image-assets/{asset_id}/retry
```

文档级失败图片重试 payload：

```json
{
  "only_retryable": true,
  "max_assets": 20
}
```

服务端执行：

```text
1. 检查文档存在、未删除、未处于整文档 ingest 中。
2. 查询 status=failed 的图片资产。
3. 如果 only_retryable=true，只选 retryable=true 的资产。
4. 从原始文档重新 parse 出 image_assets。
5. 用 asset_uid / asset_id / content_hash 找到待重试资产对应的图片 payload。
6. 只对这些资产调用 qwen-vl-ocr。
7. 成功后生成 image chunk、写向量、写 asset-chunk link。
8. 更新 asset 状态和文档汇总字段。
```

该流程仍需要重新解析原始文档来拿到图片二进制，但不会重跑正文 chunk，也不会调用成功图片的 OCR。

## Chunk 写入策略

### 第一阶段推荐：追加 image chunk

对于过去失败、现在重试成功的图片，原本没有 image chunk，因此可以直接追加：

```text
chunk_index = 当前文档最大 chunk_index + 1
metadata_json.source_modalities 包含 image_asset
metadata_json.asset_ids 包含 asset_id
KnowledgeImageAssetChunk 记录 image_asset_id -> chunk_id
```

优点：

```text
实现简单。
不会影响已有正文 chunk 和成功图片 chunk。
不需要重算整篇文档 embedding。
```

缺点：

```text
chunk 列表顺序不一定严格等于原文档顺序。
```

检索阶段这通常可接受，因为 image chunk 依然带有 `page_number` / `source_offset` / `location_label`。

### 第二阶段：顺序保真

如果需要在 chunk 预览中严格按原文档顺序显示，需要给 `KnowledgeChunk` 增加或复用排序信息：

```text
source_order
source_modality
asset_uid
```

重试成功后按 `source_order` 重新计算文档内 chunk_index，或前端按 `source_order` 排序。

## 老文档迁移

老文档没有 `KnowledgeImageAsset` 行。迁移策略分两层。

### 被动迁移

用户打开文档详情或点击重试失败图片时触发：

```text
1. 从原始文档重新 parse image_assets。
2. 为每张图片创建 KnowledgeImageAsset 行。
3. 扫描已有 KnowledgeChunk.metadata_json：
   - metadata_json.asset_ids
   - metadata_json.source_metadata.asset_id
4. 命中已有 image chunk 的 asset 标为 completed，并建立 KnowledgeImageAssetChunk。
5. 未命中且文档 image_asset_failed_count > 0 的 asset 标为 failed 或 unknown_failed_candidate。
```

注意：如果老文档有 `image_asset_failed_count=2`，但无法从历史记录知道是哪两张失败，只能把未命中的图片作为候选失败。

这种情况下第一次“定向重试”可能仍然会处理多于真实失败数的图片，但它不会重跑已能匹配到 completed chunk 的图片。完成一次迁移后，后续就能精确重试。

### 主动迁移

提供后台维护任务：

```text
POST /api/knowledge/image-assets/backfill
```

用于批量为历史文档建立图片明细。第一版不必暴露给普通用户，可以只作为开发/维护接口。

## API 设计

### 列出文档图片

```text
GET /api/knowledge/documents/{document_id}/image-assets
```

返回：

```json
[
  {
    "id": 1,
    "document_id": 10,
    "asset_id": "pdf-page-3-image-2",
    "location_label": "PDF 第 3 页图片 2",
    "page_number": 3,
    "status": "failed",
    "retryable": true,
    "attempt_count": 1,
    "error_code": "DASHSCOPE_REQUEST_TIMEOUT",
    "error_message": "qwen-vl-ocr request timed out.",
    "image_type": null,
    "confidence": null,
    "chunk_ids": []
  }
]
```

### 重试文档失败图片

```text
POST /api/knowledge/documents/{document_id}/image-assets/retry-failed
```

返回：

```json
{
  "document": {},
  "job": {
    "id": 123,
    "type": "knowledge_image_retry",
    "status": "pending"
  },
  "queued_asset_count": 2
}
```

### 重试单张图片

```text
POST /api/knowledge/image-assets/{image_asset_id}/retry
```

约束：

```text
status 必须是 failed 或 skipped。
默认只允许 retryable=true。
如果用户选择“强制重试”，需要前端明确提示可能再次失败或产生费用。
```

## 前端设计

文档卡片：

```text
图片 37/44 · 37 chunks · 失败 7
三点菜单：
  重试失败图片
  查看图片明细
  重新处理整篇文档图片
  删除
```

文档详情：

```text
图片资源表
  位置
  状态
  是否可重试
  尝试次数
  错误原因
  chunk 数
  操作
```

按钮文案区分：

```text
重试失败图片
  只处理失败图片。

重新处理整篇文档图片
  临时兜底，会重新解析并重建整份文档索引。
```

## 成本与限流

重试前应显示预计处理数量：

```text
将重试 7 张失败图片。
只会调用失败图片的 qwen-vl-ocr，不会重跑 37 张已成功图片。
```

服务端建议限制：

```text
max_retry_assets_per_job
daily_image_retry_budget
retryable=false 时默认不进入批量重试
同一文档同时只能存在一个 image retry job
```

## 测试计划

后端单元测试：

```text
解析文档后会 upsert KnowledgeImageAsset。
图片成功时写 completed、token_usage 和 asset-chunk link。
图片低价值时写 skipped，不生成 chunk。
图片超时时写 failed、retryable=true。
图片过大时写 failed/skipped、retryable=false。
文档汇总字段由明细聚合得到。
```

定向重试测试：

```text
只选择 status=failed 且 retryable=true 的图片。
不会调用 completed 图片的 extractor。
重试成功后只新增对应 image chunk。
重试失败后 attempt_count 增加，错误更新。
找不到原始文件时返回明确错误。
同一文档已有活跃 retry job 时返回已有 job。
```

迁移测试：

```text
老文档可以从现有 image chunk metadata 反推 completed asset。
无法反推的图片进入 unknown/failed candidate。
迁移后重复执行不会创建重复 asset。
```

前端测试：

```text
失败图片数大于 0 时显示“重试失败图片”。
无失败图片时不显示批量重试入口。
图片明细表正确展示错误原因和重试状态。
点击重试后刷新文档和后台任务。
```

## 分阶段实现

### Phase 1：表结构和新文档明细

```text
1. 新增 KnowledgeImageAsset 和 KnowledgeImageAssetChunk。
2. ingest 时写入图片明细和处理状态。
3. 文档汇总字段改为从明细聚合更新。
4. 保持现有文档级 retry 接口不变。
```

### Phase 2：定向重试失败图片

```text
1. 新增 image retry job/API。
2. 从原始文件重新抽取图片 payload。
3. 只调用 failed/retryable 图片。
4. 重试成功后追加 image chunk 和 embedding。
5. 前端三点菜单接入“重试失败图片”。
```

### Phase 3：历史文档迁移和图片明细 UI

```text
1. 被动 backfill 老文档图片资产。
2. 从现有 image chunk metadata 反推成功图片。
3. 文档详情展示图片资源表。
4. 支持单张图片重试。
```

### Phase 4：顺序保真和成本面板

```text
1. 为 chunk 增加 source_order 或前端源顺序排序。
2. 支持预算统计和 token usage 汇总。
3. 支持按错误类型筛选、批量重试、强制重试。
```

## 当前临时方案的处理

当前已实现的 `retry-image-processing` 应保留，但改名或改文案为：

```text
重新处理整篇文档图片
```

它适合作为：

```text
老文档无法建立明细时的兜底。
parser 或 chunk 策略变化后的全量修复。
用户明确想重建整篇文档索引时的维护操作。
```

它不应继续被称为“重试图片”，否则用户会误以为只重试失败图片。

