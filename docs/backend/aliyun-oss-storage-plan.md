# 阿里云 OSS 云存储使用规划

调研日期：2026-06-12

本文规划 AiMemo 后续接入阿里云对象存储 OSS（Object Storage Service）的使用方式。结论先行：第一版建议继续采用“本地优先 + 云端对象存储”的策略，本地 SQLite 仍然是运行时主数据库；云端通过 manifest 和按笔记拆分的 JSON 对象保存完整笔记数据，通过独立对象保存附件、音频、图片、导出包和备份快照。

## 目标

- 支持笔记附件、图片、语音、导出包和备份文件的云端保存。
- 支持完整笔记内容、元数据和删除状态的云端增量同步。
- 避免把云存储和业务逻辑强绑定，后续可以替换为 S3 兼容服务。
- 默认私有访问，所有上传和下载都通过后端签名或临时凭证控制。
- 早期优先控制复杂度和误收费风险，不急于使用 CDN、传输加速、图片处理等增值能力。

## 基础概念

### OSS

OSS 是阿里云的对象存储服务。它适合保存“文件型数据”，例如图片、录音、PDF、导出 ZIP、SQLite 备份文件等。它不是关系数据库，不适合直接替代 notes、tags、conversations 这些结构化表。

在 AiMemo 里可以这样理解：

```text
SQLite / 后续云数据库
  保存本地可直接运行的笔记正文、标题、标签、索引状态、文件元数据、同步状态

OSS
  保存云端同步副本，例如笔记 JSON、图片、音频、附件、导出包、加密备份
```

### Bucket

Bucket 是 OSS 里的存储空间，类似一个顶层文件仓库。建议为 AiMemo 单独创建一个私有 Bucket，例如：

```text
aimemo-prod
aimemo-dev
```

第一版建议只使用私有 Bucket，不开放公共读写。

### Object

Object 是 OSS 中的单个文件。Object 没有真正的目录，`/` 只是对象 Key 的命名约定。

示例：

```text
users/{user_id}/notes/{note_id}/attachments/{file_id}.png
users/{user_id}/voice/{conversation_id}/{message_id}.wav
users/{user_id}/exports/{export_id}.zip
users/{user_id}/backups/{date}/aimemo.sqlite.enc
```

### Region 和 Endpoint

Region 是数据所在地域，例如华东 1、华东 2、华北 2。Endpoint 是访问该 Region OSS 的服务地址。

选择原则：

- 用户主要在国内使用，优先选择离用户近、服务稳定、价格合适的国内 Region。
- Bucket 创建后 Region 通常不作为日常配置频繁变更。
- 后端服务和 OSS 在同一 Region 时，可减少延迟和内网访问成本。

### LRS 和 ZRS

LRS 是本地冗余，数据冗余在同一个可用区内。成本较低，但如果该可用区不可用，相关数据可能不可访问。

ZRS 是同城冗余，数据冗余在同一地域的多个可用区。可用性更高，价格通常更贵。

AiMemo 第一版建议：

- 个人使用、早期验证：标准存储 LRS 即可。
- 如果未来做多用户云同步，且附件和备份具有明显不可丢价值，再考虑标准存储 ZRS。

## 存储类型

阿里云 OSS 按数据访问频率和恢复速度提供多种存储类型。可以把它们理解为从“热数据”到“冷数据”的分层。

| 类型 | 适合什么数据 | 是否实时访问 | 关键成本注意点 | AiMemo 建议 |
| --- | --- | --- | --- | --- |
| 标准存储 Standard | 经常打开的图片、附件、音频、当前备份 | 是 | 单价较高，但没有最低保存时间 | 默认使用 |
| 低频访问 IA | 偶尔访问的旧附件、旧图片 | 是 | 小文件按至少 64 KB 计费，最低存储 30 天，读取会有取回费用 | 谨慎用于较大且不常访问的文件 |
| 归档 Archive | 长期保存、很少访问的备份或导出 | 需要直读或解冻后访问 | 小文件按至少 64 KB 计费，最低存储 60 天，取回收费 | 适合较老备份，不适合日常附件 |
| 冷归档 Cold Archive | 几乎不会访问但必须留存的数据 | 需要解冻，通常 1 到 12 小时 | 最低存储 180 天，取回和解冻有费用 | 只用于长期历史备份 |
| 深度冷归档 Deep Cold Archive | 极长期、极少取回的数据 | 需要解冻，通常 12 或 48 小时 | 最低存储 180 天，恢复慢 | 第一版不建议启用 |

### 低频访问 IA

IA 是 Infrequent Access，意思是低频访问。它的存储单价比标准存储低，但不是“无脑更便宜”。

它有几个容易踩坑的点：

- 适合平均每月访问 1 到 2 次或更少的对象。
- 不足 64 KB 的文件也按 64 KB 计费。
- 最低存储时间是 30 天，提前删除或转换可能产生不足时长费用。
- 读取数据时会产生数据取回费用。

因此，AiMemo 不应该把所有附件默认转成 IA。大量小图标、小 Markdown 附件、小 JSON 片段转 IA 可能省不了钱，甚至更贵。

### 归档 Archive

归档适合长期保存但很少读取的数据，例如历史备份、旧导出包、合规留存文件。

它的特点：

- 存储费用比 IA 更低。
- 最低存储时间是 60 天。
- 读取前通常需要解冻，阿里云也支持归档直读，但会产生对应取回费用。
- 不适合用户点开笔记时马上要看到的图片或附件。

AiMemo 可以把较老的加密备份放到 Archive，但不应该把普通笔记附件自动归档。

### 冷归档和深度冷归档

冷归档和深度冷归档进一步降低存储单价，但取回更慢，最低存储时间更长，适合“几乎永远不会访问，但不能删除”的数据。

AiMemo 第一版不建议默认使用深度冷归档。原因是个人笔记产品更强调可恢复和可理解，用户通常不希望点恢复备份后等待半天。冷归档可以作为高级选项，用于一年以上的历史备份。

## 计费理解

OSS 的账单主要由几部分组成：

- 存储费用：文件放在 Bucket 里多久、放了多少 GB、属于哪种存储类型。
- 请求费用：上传、下载、读取元信息、列举对象等 API 调用次数。
- 外网流出流量费用：从 OSS 下载到公网客户端时产生。
- 数据取回费用：读取 IA、归档、冷归档等低成本存储类型时产生。
- 增值服务费用：CDN、图片处理、传输加速、DDoS 防护等。

第一版成本控制原则：

- 先按量付费，不急着购买资源包。
- 不默认开 CDN、传输加速、图片处理。
- Bucket 保持私有，避免被公开链接刷流量。
- 对导出包、临时上传碎片、过期文件设置生命周期删除。
- 数据量稳定后，再根据账单决定是否购买存储包或 SCU。

## AiMemo 数据分层

### 仍然放数据库的数据

- 笔记标题、正文、摘要、标签。本地数据库仍然是 App 读取和编辑笔记的主数据源。
- note、conversation、memory、job 等业务表。
- chunk、embedding 状态、向量索引元数据。
- 附件的元信息：文件名、大小、MIME、hash、object key、存储类型、上传状态。
- 同步状态：本地版本、云端版本、冲突标记、最后同步时间。

### 放 OSS 的数据

- 笔记 JSON：每条笔记一个云端 JSON 对象，保存标题、正文、标签、摘要、状态和版本信息。
- 同步 manifest：保存全局版本和每条笔记的轻量标志项，用于低成本判断是否需要增量拉取。
- 笔记附件：图片、PDF、Office 文件、网页剪藏原始资源。
- 语音文件：ASR 输入音频、TTS 输出音频、对话录音。
- OCR 原图：知识库图片转文本的原始图片。
- 导出包：Markdown、JSON、ZIP、HTML 导出。
- 加密备份：本地 SQLite 或用户数据快照。

## 推荐对象 Key 设计

```text
users/{user_id}/sync/manifest.json
users/{user_id}/sync/notes/{note_id}.json
users/{user_id}/notes/{note_id}/attachments/{file_id}.{ext}
users/{user_id}/notes/{note_id}/images/{image_id}.{ext}
users/{user_id}/voice/{conversation_id}/{message_id}.{ext}
users/{user_id}/exports/{export_id}.{ext}
users/{user_id}/backups/{yyyy}/{mm}/{dd}/{backup_id}.sqlite.enc
tmp/uploads/{upload_id}/{part_name}
```

设计原则：

- Key 中带 `user_id`，为未来多用户隔离做准备。
- Key 中带业务对象 ID，便于排查和按前缀配置生命周期。
- 不把原始文件名直接作为唯一 Key，避免重名、非法字符和隐私泄露。
- 数据库记录原始文件名，OSS Key 使用系统生成 ID。

## 云端同步策略

AiMemo 的云端同步不应该每次都全量上传或全量下载。推荐把用户提出的“全局标志 + 单条笔记标志”明确设计为同步 manifest。

### Manifest

`manifest.json` 是一个很小的 JSON 文件，拉取和上传前优先读取它。它只保存可比较的轻量信息，不保存完整笔记正文。

示例结构：

```json
{
  "schema_version": 1,
  "user_id": "local-user-or-account-id",
  "global_revision": 42,
  "updated_at": "2026-06-13T10:30:00Z",
  "device_id": "desktop-a",
  "notes": {
    "note_001": {
      "revision": 7,
      "content_hash": "sha256:...",
      "updated_at": "2026-06-13T10:20:00Z",
      "deleted": false,
      "object_key": "users/u1/sync/notes/note_001.json"
    },
    "note_002": {
      "revision": 3,
      "content_hash": "sha256:...",
      "updated_at": "2026-06-12T22:10:00Z",
      "deleted": true,
      "object_key": "users/u1/sync/notes/note_002.json"
    }
  }
}
```

关键字段含义：

- `global_revision`: 任意笔记新增、修改、删除后都递增。拉取前先比较这个值，如果和本地记录一致，就不继续下载笔记对象。
- `notes[note_id].revision`: 单条笔记的版本号。只有该值比本地已同步版本新时，才下载对应 `note_id.json`。
- `content_hash`: 笔记内容的 hash，用于确认内容是否真的变化，也可用于上传后校验。
- `deleted`: 软删除标记。删除也要同步，不能只依赖对象是否存在。
- `object_key`: 完整笔记 JSON 所在位置。

### 单条笔记 JSON

每条笔记一个 JSON 对象，保存完整笔记数据，但不直接内嵌大附件。

```json
{
  "schema_version": 1,
  "id": "note_001",
  "title": "示例笔记",
  "content": "Markdown or rich text payload",
  "summary": "...",
  "tags": ["cloud", "sync"],
  "status": "active",
  "created_at": "2026-06-13T09:00:00Z",
  "updated_at": "2026-06-13T10:20:00Z",
  "revision": 7,
  "content_hash": "sha256:...",
  "attachments": [
    {
      "id": "file_001",
      "object_key": "users/u1/notes/note_001/attachments/file_001.png",
      "filename": "image.png",
      "content_type": "image/png",
      "size_bytes": 12345,
      "sha256": "..."
    }
  ]
}
```

这样做的好处是：拉取时先下载很小的 manifest，再按需下载发生变化的笔记 JSON；附件只有在笔记引用变化、用户打开或本地缺失时再下载。

### 拉取策略

自动拉取和手动拉取都走同一套增量流程：

```text
读取远端 manifest.json
  -> 与本地 last_remote_global_revision 比较
  -> 如果一致，结束
  -> 如果不一致，逐条比较 note revision / content_hash / deleted
  -> 下载新增或远端更新的 note JSON
  -> 对本地缺失的附件只记录元数据，可延迟到打开时再下载
  -> 应用删除标记
  -> 更新本地 last_remote_global_revision
  -> 按 updated_at desc 重新排序笔记列表
```

必要触发条件：

- 远端 `global_revision` 与本地记录不同。
- 某条远端 note 的 `revision` 高于本地已同步版本。
- 本地没有远端 manifest 中出现的 note。
- 远端 note 的 `deleted=true`，但本地仍为 active。

### 上传策略

本地每次新增、编辑、删除笔记时，都更新本地的同步标志：

```text
note.local_revision += 1
note.content_hash = sha256(normalized_note_payload)
note.sync_status = dirty
local_manifest.global_revision += 1
```

自动上传和手动上传默认只上传 dirty 项：

```text
扫描本地 dirty notes
  -> 为每条 dirty note 生成 note JSON
  -> 上传 note JSON 和新增附件对象
  -> 更新远端 manifest 中对应 note 的 revision / content_hash / deleted / updated_at
  -> 上传新的 manifest.json
  -> 标记本地 note 为 synced
```

用户提出的“不需要去云端检查”适合单设备场景，但多设备下有覆盖风险。默认实现建议在上传 manifest 前读取远端 manifest 的 ETag 或 revision：

- 如果远端 manifest 没变，可以直接提交上传。
- 如果远端 manifest 已变，先执行一次增量拉取和冲突检测，再决定是否上传。
- 如果用户选择“强制上传”，需要在 UI 中明确提示可能覆盖远端更新。

### 冲突策略

当同一条笔记在本地和远端都发生了未同步修改时，不应该静默覆盖。

第一版建议采用保守策略：

- 不自动合并正文。
- 保留本地版本和远端版本。
- 将笔记标记为 `conflicted`。
- UI 提供“保留本地”“使用云端”“另存为副本”三个动作。

后续可以再考虑 Markdown 级别的三方合并，但第一版先保证不丢数据。

### 排序策略

笔记列表应当无论是否开启云同步，都按明确字段排序。默认建议：

```text
pinned desc
updated_at desc
created_at desc
id asc
```

云端拉取完成后，导入或更新的笔记也必须写入 `updated_at`，让本地列表顺序与用户预期一致。

## 同步设置面板

前端可以提供一个“云同步”设置面板，允许用户控制自动同步频率和手动动作。

建议配置项：

- 云同步开关。
- 云服务提供商：第一版固定为 `aliyun_oss`，后续可扩展。
- Bucket、Region、Endpoint。
- 用户命名空间：`user_id` 或 `sync_namespace`，用于隔离不同用户的数据前缀。
- 上传周期：例如关闭、5 分钟、15 分钟、30 分钟、1 小时、仅手动。
- 拉取周期：例如启动时拉取、5 分钟、15 分钟、30 分钟、1 小时、仅手动。
- 网络策略：仅 Wi-Fi、允许移动热点、失败后指数退避。
- 手动动作：立即上传、立即拉取、先拉取再上传、查看同步状态。
- 冲突策略：发现冲突时暂停同步并提示，默认不自动覆盖。

自动触发建议：

- 应用启动后延迟拉取一次。
- 用户停止编辑一段时间后上传 dirty notes，而不是每输入一个字符就上传。
- 应用退出或进入后台前尝试上传一次，但不能阻塞退出太久。
- 周期任务失败后记录错误，并按退避策略重试。

## 认证和用户命名空间

用户需要提供能访问自己 OSS Bucket 的认证信息，以及一个稳定的用户命名空间。

第一版个人使用可以采用：

- `ALIYUN_ACCESS_KEY_ID`
- `ALIYUN_ACCESS_KEY_SECRET`
- `storage.aliyun_oss.bucket`
- `storage.aliyun_oss.region`
- `storage.sync.user_id`

正式多用户版本建议改为：

- 用户登录 AiMemo 账号。
- 后端为当前用户签发 STS 临时凭证或预签名 URL。
- OSS RAM 权限限制到 `users/{user_id}/` 前缀。
- 客户端不保存长期 AccessKey Secret。

`user_id` 不等于阿里云账号 ID。它是 AiMemo 用来隔离同步数据的命名空间，可以由用户账号、设备配置或后端账号系统生成。

## 上传和下载流程

### 上传

推荐使用“后端签名，客户端直传 OSS”的模式：

```text
客户端请求创建上传任务
  -> 后端校验用户、文件大小、MIME、业务归属
  -> 后端生成临时上传凭证或预签名 URL
  -> 客户端直接上传到 OSS
  -> 客户端通知后端上传完成
  -> 后端记录文件元数据并绑定到 note/conversation/export
```

这样可以避免大文件全部经过 FastAPI 后端，降低后端带宽和内存压力。

### 下载

下载使用短有效期签名 URL：

```text
客户端请求附件下载地址
  -> 后端校验权限
  -> 后端生成短有效期 signed URL
  -> 客户端从 OSS 下载
```

签名 URL 不应该长期缓存到笔记正文里。数据库只保存 object key，真正访问时再换取临时 URL。

## 生命周期策略

生命周期规则可以按对象前缀或标签自动转换存储类型，或自动删除对象。

第一版建议从保守策略开始：

| 前缀 | 初始类型 | 生命周期建议 | 原因 |
| --- | --- | --- | --- |
| `users/*/notes/*/attachments/` | Standard | 不自动转冷 | 用户随时可能打开笔记附件 |
| `users/*/notes/*/images/` | Standard | 不自动转冷 | 图片常用于笔记展示 |
| `users/*/voice/` | Standard | 30 到 90 天后删除或转 IA，取决于是否需要回放 | 语音通常体积大，长期价值不一定高 |
| `users/*/exports/` | Standard | 7 到 30 天后删除 | 导出包可重新生成 |
| `users/*/backups/` | Standard | 30 天后 IA，90 或 180 天后 Archive | 备份需要长期保留但很少读取 |
| `tmp/uploads/` | Standard | 1 到 3 天后删除 | 清理未完成上传和临时文件 |

不建议第一版自动使用 Deep Cold Archive。等备份策略稳定、恢复流程成熟后再开放高级配置。

## 后端配置草案

```json5
{
  "storage": {
    "provider": "aliyun_oss",
    "aliyun_oss": {
      "region": "cn-hangzhou",
      "bucket": "aimemo-dev",
      "endpoint": "https://oss-cn-hangzhou.aliyuncs.com",
      "access_key_id_env": "ALIYUN_ACCESS_KEY_ID",
      "access_key_secret_env": "ALIYUN_ACCESS_KEY_SECRET",
      "default_storage_class": "Standard",
      "signed_url_ttl_seconds": 900,
      "max_upload_bytes": 104857600
    },
    "sync": {
      "enabled": false,
      "user_id": "local-user",
      "pull_interval_seconds": 900,
      "push_interval_seconds": 900,
      "pull_on_startup": true,
      "push_on_idle_seconds": 30,
      "conflict_policy": "keep_both",
      "manifest_key": "users/local-user/sync/manifest.json"
    }
  }
}
```

AccessKey 不应该写入 `config.json5`。本地开发可以放 `.env`，正式部署应使用环境变量、RAM 角色或 STS。

## 数据模型草案

后续可新增类似 `cloud_objects` 或 `file_objects` 的表：

```text
cloud_objects
  id
  owner_user_id
  bucket
  object_key
  provider
  region
  storage_class
  content_type
  size_bytes
  sha256
  original_filename
  status
  created_at
  uploaded_at
  last_accessed_at
```

笔记表后续需要补充同步字段：

```text
notes
  cloud_revision
  local_revision
  last_synced_revision
  content_hash
  sync_status
  sync_conflict_id
  cloud_object_key
  last_synced_at
```

本地还需要一张同步状态表：

```text
sync_state
  id
  provider
  user_id
  manifest_key
  last_remote_global_revision
  last_manifest_etag
  last_pull_at
  last_push_at
  last_error
```

笔记附件再通过关联表绑定：

```text
note_attachments
  id
  note_id
  cloud_object_id
  display_name
  sort_order
  created_at
```

这样可以做到业务对象和云文件解耦：以后即使从 OSS 迁移到 S3 兼容服务，也不需要重写笔记表。

## 安全策略

- Bucket 默认私有。
- 不使用长期公开 URL。
- 用户下载必须先经过后端鉴权。
- 上传前由后端限制文件大小、文件类型和业务归属。
- AccessKey 只放环境变量或云端密钥系统。
- 多用户版本必须在服务端校验 `user_id` 和 object key 前缀一致。
- 备份文件建议客户端或服务端加密后再上传。

## 实施阶段

### 阶段 1：对象存储抽象

- 新增 `CloudObjectStorageProvider` 接口。
- 实现 `AliyunOssStorageProvider`。
- 支持生成上传签名、下载签名、删除对象、读取对象元信息。
- 新增云对象元数据表。

### 阶段 2：Manifest 和笔记 JSON 同步

- 新增 `manifest.json` 的读取、比较和写入逻辑。
- 为 note 增加本地 revision、content hash、sync status 和 cloud object key。
- 支持手动拉取、手动上传和启动后拉取。
- 默认只同步 dirty notes 和远端 revision 变化的 notes。
- 冲突时保留双版本，不自动覆盖。

### 阶段 3：笔记附件

- 为 note 增加附件 API。
- 前端支持上传、展示、下载、删除附件。
- 默认使用 Standard 存储。
- 删除笔记时先软删除附件关联，不立即删除 OSS 对象。

### 阶段 4：同步设置面板

- 前端提供云同步开关、上传周期、拉取周期和手动同步按钮。
- 展示最近同步时间、dirty 数量、冲突数量和最近错误。
- 支持“仅手动同步”模式，避免用户不清楚后台流量。

### 阶段 5：导出和备份

- 导出包写入 `exports/` 前缀，并配置自动过期。
- 支持加密备份上传到 `backups/` 前缀。
- 为备份配置 IA / Archive 生命周期。

### 阶段 6：更完整的云同步

- 处理跨设备冲突、版本历史和增量同步优化。
- 增加远端 manifest 分片，避免笔记数量很大时单个 manifest 过大。
- 增加附件延迟下载和本地缓存清理策略。

## 暂不做

- 不把 OSS 当数据库使用。
- 不默认公开 Bucket。
- 不默认启用 CDN。
- 不默认使用 Deep Cold Archive。
- 不在第一版自动合并冲突笔记正文。
- 不在第一版做多云迁移工具，只保留接口抽象和 S3 兼容设计余地。

## 参考资料

- 阿里云 OSS 计费概述：https://www.alibabacloud.com/help/zh/oss/billing-overview
- 阿里云 OSS 存储类型：https://www.alibabacloud.com/help/zh/oss/user-guide/overview-53/
- 阿里云 OSS 存储费用：https://www.alibabacloud.com/help/zh/oss/storage-fees
- 阿里云 OSS 生命周期规则：https://www.alibabacloud.com/help/zh/oss/user-guide/lifecycle-rules-based-on-the-last-modified-time
