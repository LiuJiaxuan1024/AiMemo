# AiMemo 全域云存储规划

本文规划 AiMemo 从“笔记可 OSS 同步”扩展到“整套个人知识中心可同步、可备份、可迁移”的数据存储方案。它建立在现有 [阿里云 OSS 云存储使用规划](./aliyun-oss-storage-plan.md) 和 [云存储模块设计](./cloud-storage-module-design.md) 之上，不改变一个核心原则：

> AiMemo 仍然是本地优先应用。本地 SQLite 和本地文件是运行时主数据源；OSS 保存可恢复、可迁移、可分享的云端副本和大对象。

## 目标

- 让笔记、对话、知识库、长期记忆、附件、语音配置、导出和备份逐步进入同一套云端存储体系。
- 支持多设备之间增量同步，而不是全量上传或全量拉取。
- 保留本地离线可用能力，云端不可用时不影响本地编辑和对话。
- 避免把派生索引、临时任务和调试 state 当成必须同步的用户数据。
- 为未来多用户、端到端加密、S3 兼容存储和云数据库留出边界。

## 非目标

- 第一阶段不把 OSS 当在线数据库使用。
- 第一阶段不实现多人实时协作。
- 第一阶段不要求云端直接支持复杂查询、向量检索或图检索。
- 第一阶段不把 LangGraph checkpoint 作为跨设备继续执行的默认能力。
- 第一阶段不默认公开任何对象，不用公共读 Bucket 承载分享。

## 总体原则

### 本地优先

所有用户操作先写本地数据库或本地文件，再标记为待同步。同步失败只影响云端副本，不应让用户无法继续使用本地 AiMemo。

### 云端保存“事实数据”，派生数据可重建

云端优先保存用户输入、用户确认、用户上传的事实数据。Embedding、chunk、OCR 派生文本、摘要、标签等可以同步，但必须能通过原始数据重新生成。

### 小数据 JSON 化，大文件对象化

- 结构化业务数据保存为 JSON 对象。
- 图片、音频、PDF、Office、导出包、SQLite 备份保存为独立对象。
- JSON 中只保存大文件引用，不内嵌 base64 大文件。

### Manifest 驱动增量同步

每类业务域维护自己的轻量 manifest。拉取时先比较 manifest，不直接扫描和下载全部对象。

```text
users/{user_id}/sync/global_manifest.json
users/{user_id}/sync/domains/notes_manifest.json
users/{user_id}/sync/domains/conversations_manifest.json
users/{user_id}/sync/domains/knowledge_manifest.json
users/{user_id}/sync/domains/memories_manifest.json
```

### 删除也是一种同步事件

所有可同步业务对象都必须支持软删除标记。不能因为云端对象仍然存在，就在另一台设备上把已删除内容恢复回来。

### 冲突默认不静默覆盖

同一个对象在两个设备上都被修改时，第一版优先 `keep_both` 或进入冲突列表，由用户确认。只有明确标记为可自动合并的数据才自动合并。

## 数据分类

### A 类：必须同步的核心用户数据

这些数据是用户真正关心的内容，应进入增量同步。

| 数据 | 本地来源 | 云端形态 | 同步策略 |
| --- | --- | --- | --- |
| 笔记 | `notes` | 每条 note 一个 JSON | 已实现/继续完善 |
| 对话列表 | `conversations` | 每条 conversation 一个 JSON | 增量同步 |
| 对话消息 | `chat_messages` | 按 conversation 分片 JSON | 增量同步 |
| 片段追问 | 消息 JSON / followup metadata | 跟随消息保存 | 增量同步 |
| 长期记忆 | `long_term_memories` | 每条 memory 一个 JSON | 增量同步 |
| 知识空间 | `knowledge_spaces` | 每个 space 一个 JSON | 增量同步 |
| 知识文档元数据 | `knowledge_documents` | 每个 document 一个 JSON | 增量同步 |
| 知识库挂载关系 | `conversation_knowledge_mounts` | 跟随 conversation 或独立 JSON | 增量同步 |
| 声线配置 | `voice_profiles` | 每条 profile 一个 JSON | 增量同步 |
| 用户运行时偏好 | `runtime_config` | config JSON | 增量同步 |

### B 类：必须保存的大对象

这些数据不适合写入 JSON 正文，应作为 OSS 对象保存，并在业务 JSON 中引用。

| 数据 | 本地来源 | 云端对象 |
| --- | --- | --- |
| 聊天附件 | `data/uploads` / `chat_attachments.storage_path` | `objects/chat_attachments/...` |
| 知识库原始文档 | `data/knowledge/documents` | `objects/knowledge_documents/...` |
| 知识库图片资产 | 从 PDF/PPTX/DOCX 提取 | `objects/knowledge_images/...` |
| TTS 音频缓存 | 语音服务输出 | `objects/voice/tts/...` |
| ASR 输入录音 | 语音输入 | `objects/voice/asr/...` |
| 导出 HTML/ZIP | 对话导出、笔记导出 | `exports/...` |
| SQLite 加密备份 | 本地数据库快照 | `backups/...` |

### C 类：可同步但可重建的派生数据

这些数据同步后能提升恢复速度，但不能被视为唯一可信来源。

| 数据 | 本地来源 | 建议 |
| --- | --- | --- |
| 笔记 summary/tags | `notes.summary/tags` | 可同步，拉取后可按模型版本重算 |
| 笔记 chunk | `note_chunks` | 第二阶段再同步，第一阶段可本地重建 |
| 知识 chunk | `knowledge_chunks` | 可同步文本 chunk，但 embedding 可重建 |
| 图片 OCR 文本 | `knowledge_image_assets` / chunks | 可同步，保留 extractor/model/prompt_version |
| 对话 summary | `conversations.summary` | 可同步，冲突时可重算 |
| 附件 derivative | `chat_attachment_derivatives` | 可同步，保留 source_hash |

### D 类：本地运行状态，不默认同步

这些数据更像本机执行现场，不应默认跨设备同步。

| 数据 | 原因 |
| --- | --- |
| `jobs` 当前队列 | 包含本机锁、运行中任务、失败堆栈，跨设备意义弱 |
| background task 运行状态 | 依赖本机进程 |
| LangGraph checkpoint | 可能包含大量 prompt、工具 state、隐私和本机路径 |
| Graph 调试 state | 调试用途强，隐私风险高 |
| 临时上传分片 | 生命周期短 |
| 本机路径配置 | 迁移到另一台机器可能无效 |

这些数据可以进入“加密整库备份”，但不进入默认增量同步。

## 云端目录规划

建议把同步数据和大对象分开。

```text
users/{user_id}/
  sync/
    global_manifest.json
    domains/
      notes_manifest.json
      conversations_manifest.json
      knowledge_manifest.json
      memories_manifest.json
      voice_manifest.json
      config_manifest.json
    notes/{note_id}.json
    conversations/{conversation_id}/conversation.json
    conversations/{conversation_id}/messages/{shard_id}.json
    memories/{memory_id}.json
    knowledge/spaces/{space_id}.json
    knowledge/documents/{document_id}.json
    voice/profiles/{profile_id}.json
    config/runtime.json
  objects/
    chat_attachments/{conversation_id}/{attachment_id}/{sha256}.{ext}
    knowledge_documents/{space_id}/{document_id}/{sha256}.{ext}
    knowledge_images/{document_id}/{asset_uid}.{ext}
    voice/asr/{conversation_id}/{message_id}/{object_id}.{ext}
    voice/tts/{profile_id}/{sha256}.{ext}
  exports/
    conversations/{conversation_id}/{export_id}.html
    notes/{note_id}/{export_id}.md
  backups/
    sqlite/{yyyy}/{mm}/{dd}/{backup_id}.sqlite.enc
```

说明：

- `sync/` 保存可增量同步的业务 JSON。
- `objects/` 保存被业务 JSON 引用的大文件。
- `exports/` 保存用户主动导出的分享文件。
- `backups/` 保存整库快照，恢复语义和增量同步不同。

## Global Manifest

`global_manifest.json` 是所有域的入口，只保存每个业务域是否发生变化。

```json
{
  "schema_version": 1,
  "user_id": "local-user",
  "updated_at": "2026-06-14T12:00:00Z",
  "global_revision": 128,
  "domains": {
    "notes": {
      "revision": 42,
      "manifest_key": "users/local-user/sync/domains/notes_manifest.json",
      "etag": "..."
    },
    "conversations": {
      "revision": 31,
      "manifest_key": "users/local-user/sync/domains/conversations_manifest.json",
      "etag": "..."
    },
    "knowledge": {
      "revision": 18,
      "manifest_key": "users/local-user/sync/domains/knowledge_manifest.json",
      "etag": "..."
    }
  }
}
```

拉取流程：

1. 读取 `global_manifest.json`。
2. 比较本地记录的 `domains.{name}.revision`。
3. 只下载变化域的 domain manifest。
4. 再根据 domain manifest 下载变化对象。

上传流程：

1. 扫描本地 dirty 对象。
2. 上传对应业务 JSON 或大对象。
3. 更新 domain manifest。
4. 最后更新 global manifest。

## Domain Manifest

每个业务域可以按自己的对象粒度组织 manifest。

### Notes Manifest

已接近当前笔记同步设计，继续沿用：

```json
{
  "schema_version": 1,
  "domain": "notes",
  "revision": 42,
  "items": {
    "1": {
      "revision": 7,
      "content_hash": "sha256:...",
      "updated_at": "2026-06-14T12:00:00Z",
      "deleted": false,
      "object_key": "users/local-user/sync/notes/1.json"
    }
  }
}
```

### Conversations Manifest

对话数据可能很长，不建议把一个 conversation 的所有消息放进一个超大 JSON。第一版可以按 conversation 拆，再按消息数量分片。

```json
{
  "schema_version": 1,
  "domain": "conversations",
  "revision": 31,
  "items": {
    "20": {
      "revision": 12,
      "conversation_key": "users/local-user/sync/conversations/20/conversation.json",
      "message_shards": [
        {
          "shard_id": "000001-000100",
          "revision": 5,
          "object_key": "users/local-user/sync/conversations/20/messages/000001-000100.json",
          "message_count": 100
        }
      ],
      "updated_at": "2026-06-14T12:00:00Z",
      "deleted": false
    }
  }
}
```

消息同步注意点：

- 保留 `parent_id`，让消息树和分支关系可恢复。
- 保留 `checkpoint_id` 作为历史引用，但不默认同步 checkpoint 内容。
- 片段追问应跟随源消息或追问消息保存 `segment_id`、`original_text`、`position`、`user_question`、`assistant_answer`。
- 导出的 Graph/debug 内容不进入默认同步。

### Knowledge Manifest

知识库包含元数据、原始文档、大量派生 chunk 和图片资产。建议分层同步。

```json
{
  "schema_version": 1,
  "domain": "knowledge",
  "revision": 18,
  "spaces": {
    "1": {
      "revision": 4,
      "object_key": "users/local-user/sync/knowledge/spaces/1.json",
      "deleted": false
    }
  },
  "documents": {
    "6": {
      "revision": 9,
      "space_id": 1,
      "metadata_key": "users/local-user/sync/knowledge/documents/6.json",
      "source_object_key": "users/local-user/objects/knowledge_documents/1/6/sha256.pdf",
      "content_hash": "sha256:...",
      "chunk_manifest_key": "users/local-user/sync/knowledge/documents/6_chunks.json",
      "image_manifest_key": "users/local-user/sync/knowledge/documents/6_images.json",
      "deleted": false
    }
  }
}
```

建议：

- 第一阶段同步 space、document 元数据和原始文档对象。
- 第二阶段同步 chunk 文本和图片 OCR 结果，避免新设备必须重新解析大文档。
- Embedding 向量本地重建，不直接上传 sqlite-vec 内部表。
- 图片失败/警告明细可以同步，便于用户在另一台设备看到处理状态，但重试 job 不同步。

### Memories Manifest

长期记忆是高价值数据，建议单独同步。

```json
{
  "schema_version": 1,
  "domain": "memories",
  "revision": 10,
  "items": {
    "42": {
      "revision": 3,
      "content_hash": "sha256:...",
      "status": "active",
      "object_key": "users/local-user/sync/memories/42.json",
      "updated_at": "2026-06-14T12:00:00Z"
    }
  }
}
```

注意：

- `evidence_source_ids` 如果引用的是对话消息，需要允许“引用暂时不可解析”的状态。
- 冲突时不要简单用新覆盖旧；可以按 `memory_key` 做合并候选。
- 禁用/删除状态必须同步。

### Voice 和配置 Manifest

声线配置、语音模式、模型配置属于“小而重要”的配置数据。

建议：

- 同步 `voice_profiles` 的用户自定义 profile。
- 不同步远端服务的 AccessKey。
- `remote_voice_id` 可同步，但需要标记 provider 和 target model。
- 同步用户偏好、默认模型选择、界面配置时，应区分“跨设备偏好”和“本机路径配置”。

## 对象元数据

大对象应统一进入 `cloud_objects` 表或等价元数据表。

```text
cloud_objects
  owner_user_id
  provider
  bucket
  region
  object_key
  content_type
  storage_class
  size_bytes
  sha256
  original_filename
  domain
  owner_type
  owner_id
  status
  uploaded_at
  last_accessed_at
```

其中：

- `domain`: `chat`、`knowledge`、`voice`、`backup`、`export`。
- `owner_type`: `chat_attachment`、`knowledge_document`、`knowledge_image_asset` 等。
- `owner_id`: 本地业务表 ID。

这样前端可以在一个“云存储用量”页面看到大对象来源，也方便清理孤儿对象。

## 同步元数据字段

现有 `Note` 已有同步字段。后续建议把类似字段抽象为同步元数据，而不是每张表都散落一堆字段。

可选方案：

```text
sync_items
  id
  domain
  object_type
  object_id
  local_uuid
  cloud_object_key
  local_revision
  cloud_revision
  last_synced_revision
  content_hash
  sync_status
  conflict_id
  deleted
  last_synced_at
  updated_at
```

优点：

- 新业务域接入同步时不必改太多业务表。
- 同步面板可以统一展示 dirty/conflicted/error。
- 同步服务可以按 domain/object_type 扫描。

缺点：

- 查询需要 join。
- 第一版对 note 这种简单对象可以继续用业务表字段，等第二个/第三个域接入后再抽象。

建议路线：

1. 保留 `notes` 现有字段。
2. 接入 conversations 时引入 `sync_items`。
3. 后续逐步让 notes 也写入 `sync_items`，业务表字段作为兼容缓存。

## 冲突处理

### 冲突来源

- 两台设备离线修改同一笔记。
- 一台设备删除对象，另一台设备继续修改。
- 对话分支在不同设备上各自追加。
- 长期记忆被一台设备禁用，另一台设备强化同一 memory。
- 知识库同一文档被重建索引，另一台设备仍保留旧解析状态。

### 默认策略

| 类型 | 默认策略 |
| --- | --- |
| 笔记正文 | keep_both，生成冲突副本 |
| 对话消息追加 | 按 parent_id 合并为消息树 |
| 对话标题/摘要 | 保留最新，必要时可重算 |
| 长期记忆 | 按 memory_key 进入合并候选 |
| 知识库文档元数据 | 内容 hash 不同则 keep_both |
| 原始附件 | hash 相同去重，hash 不同保留两个 |
| 配置 | 按字段策略，危险字段不自动覆盖 |

### Conflict 记录

```text
sync_conflicts
  id
  domain
  object_type
  object_id
  local_revision
  remote_revision
  conflict_type
  local_snapshot_key
  remote_snapshot_key
  status
  resolution
  created_at
  resolved_at
```

冲突快照可以保存到：

```text
users/{user_id}/sync/conflicts/{conflict_id}/local.json
users/{user_id}/sync/conflicts/{conflict_id}/remote.json
```

## 加密与隐私

AiMemo 的数据包含对话、长期记忆、附件和本机路径，默认应按敏感数据处理。

第一阶段建议：

- Bucket 私有。
- RAM 用户只允许访问指定 Bucket 和指定前缀。
- 不在 OSS 保存 API Key、AccessKey、DashScope Key。
- 导出 HTML 默认不包含 Graph debug state。
- 对整库备份优先使用本地加密后上传。

后续可选：

- 为 `sync/` JSON 和 `objects/` 大对象做应用层加密。
- 用户提供本地 passphrase，云端只保存密文。
- 每个对象记录 `encryption_version`、`key_id`、`nonce`、`ciphertext_hash`。

加密后仍可使用 manifest，但 manifest 本身如果包含标题、文件名、时间等隐私信息，也需要加密或最小化。

## 备份与同步的区别

同步用于多设备持续一致，备份用于灾难恢复。

| 能力 | 同步 | 备份 |
| --- | --- | --- |
| 粒度 | 单对象/单域增量 | 整库快照 |
| 目标 | 多设备一致 | 回到某个时间点 |
| 冲突 | 需要处理 | 不处理，按快照恢复 |
| 格式 | JSON + 对象 | SQLite 加密包 |
| 频率 | 手动/周期/空闲触发 | 每日/每周/重要操作前 |

建议第一版备份策略：

- 每日最多自动生成 1 个快照。
- 手动备份不限，但提示空间占用。
- 保留最近 7 个每日备份、最近 4 个每周备份、最近 6 个每月备份。
- 备份上传到标准存储，未来可把 30 天以上备份转 Archive。

## 生命周期策略

### 标准存储

默认所有同步 JSON、当前附件、当前知识库文档、近期导出和近期备份使用 Standard。

### IA / Archive

第一版不自动迁移。后续可以提供高级选项：

- 90 天未访问的大型导出包转 IA。
- 180 天以上的加密备份转 Archive。
- 不把小 JSON、manifest、当前知识库源文件自动转 IA。

### 临时对象清理

```text
tmp/uploads/*
```

建议设置 1 到 7 天生命周期删除。

导出对象如果只是临时分享，也应支持过期删除。

## API 规划

现有：

```text
GET  /api/cloud-sync/status
POST /api/cloud-sync/pull
POST /api/cloud-sync/push
POST /api/cloud-sync/sync
```

后续扩展：

```text
GET  /api/cloud-sync/domains
GET  /api/cloud-sync/domains/{domain}/status
POST /api/cloud-sync/domains/{domain}/pull
POST /api/cloud-sync/domains/{domain}/push
POST /api/cloud-sync/domains/{domain}/rebuild-local-index

GET  /api/cloud-sync/conflicts
POST /api/cloud-sync/conflicts/{conflict_id}/resolve

GET  /api/cloud-storage/objects
POST /api/cloud-storage/objects/{object_id}/download-url
DELETE /api/cloud-storage/objects/{object_id}

GET  /api/backups
POST /api/backups
POST /api/backups/{backup_id}/restore
DELETE /api/backups/{backup_id}
```

## 前端规划

同步面板不应该只显示“上传/拉取”按钮，后续应扩展为可观察的同步中心。

建议视图：

- 总览：当前 provider、Bucket、命名空间、最后同步时间、待上传数量、冲突数量。
- 分域状态：笔记、对话、知识库、记忆、语音、配置。
- 对象用量：附件、知识库原始文档、语音、导出、备份。
- 冲突列表：按对象类型筛选，支持查看本地/远端差异。
- 备份：创建、下载、恢复、删除。
- 高级设置：周期拉取、周期上传、空闲上传、只手动同步、生命周期策略。

## 分阶段路线

### Phase 1：巩固笔记同步

- 完善现有 note manifest 和 note JSON。
- 补齐 push/pull/sync 状态展示。
- 增加冲突记录和 keep_both 处理。
- 增加本地 mock provider 回归测试。

### Phase 2：同步对话和附件

- 同步 `conversations` 和 `chat_messages`。
- 保留消息树 `parent_id`、片段追问 metadata、附件引用。
- 聊天附件进入 `objects/chat_attachments/`。
- 导出 HTML 仍是用户主动导出对象，不进入默认对话同步。

### Phase 3：同步长期记忆和配置

- 同步 `long_term_memories`。
- 同步用户自定义 voice profile。
- 同步跨设备 runtime 偏好。
- 明确哪些配置是本机配置，不上传。

### Phase 4：同步知识库源文件和索引元数据

- 同步 knowledge space、document 元数据和原始文件。
- 新设备拉取后创建本地解析/embedding 重建 job。
- 可选同步 chunk 文本和图片 OCR 结果，加速恢复。
- 不同步 sqlite-vec 原始向量表。

### Phase 5：整库加密备份

- 实现本地 SQLite 快照。
- 本地加密后上传到 `backups/`。
- 提供备份列表、恢复前检查、恢复前自动再备份。

### Phase 6：应用层加密和多设备体验

- 为同步 JSON 和对象加密。
- 增加设备 ID、设备名称、最近活跃时间。
- 支持按设备查看同步来源。
- 增加更清晰的冲突合并 UI。

## 测试策略

- 大部分同步逻辑使用 `LocalMockStorageProvider`。
- 每个 domain 都需要 push/pull/冲突/删除同步测试。
- 真实 OSS 集成测试默认跳过，只有环境变量齐全时运行。
- 大对象上传下载测试使用小文件，不产生大量云费用。
- 恢复测试必须覆盖“空数据库从云端拉取后能重建可用状态”。

建议测试文件：

```text
backend/tests/test_cloud_sync_notes.py
backend/tests/test_cloud_sync_conversations.py
backend/tests/test_cloud_sync_memories.py
backend/tests/test_cloud_sync_knowledge.py
backend/tests/test_cloud_backup_service.py
backend/tests/test_cloud_object_lifecycle.py
```

## 风险与待决策

- 是否引入全局 `sync_items` 表，还是继续在每张业务表里增加同步字段。
- 对话消息分片大小：按 100 条、500 条，还是按字节大小。
- 长期记忆冲突 UI 如何设计，避免用户被复杂合并打扰。
- 是否默认同步知识库 chunk 文本，还是只同步原始文档并重建。
- 是否启用应用层加密；如果启用，密钥恢复流程如何设计。
- 备份恢复是否允许覆盖当前数据库，还是必须恢复到新命名空间后再切换。

## 建议结论

短期不要急着把整个 SQLite 逐表同步到 OSS。更稳妥的路径是：

1. 保持本地 SQLite 为运行时主库。
2. 以 domain manifest 管理增量同步。
3. 业务事实数据写 JSON，大文件写 OSS object。
4. 派生索引优先本地重建，必要时再同步以加速恢复。
5. 整库备份作为独立能力，不和增量同步混在一起。

这样 AiMemo 可以逐步扩展到“整套内容可云端恢复”，同时避免一开始就陷入远端数据库、实时协作、加密同步、索引兼容和任务状态迁移全部耦合在一起的复杂度。
