# 云存储模块设计

本文把 [阿里云 OSS 云存储使用规划](./aliyun-oss-storage-plan.md) 落成后端模块设计。目标不是一次性实现完整云同步，而是先建立稳定的存储抽象、同步元数据和测试边界，让 AiMemo 可以从“本地优先”平滑扩展到“本地优先 + 云端增量同步”。

## 设计原则

- 本地 SQLite 仍然是运行时主数据源，所有编辑先落本地。
- 云端只保存同步副本和文件对象，不直接作为在线数据库查询。
- 业务层统一调用 AiMemo 自己的存储接口，不直接散落调用阿里云 OSS SDK。
- 笔记正文、manifest、附件、导出、备份共用同一套存储 Provider，只是对象 Key 和业务策略不同。
- 自动同步必须可关闭，第一版支持手动上传和手动拉取。
- 默认不静默覆盖冲突数据。

## 模块分层

建议分成三层：

```text
API 层
  backend/app/api/cloud_sync.py
    对前端暴露同步状态、手动上传、手动拉取、配置读取等接口

业务编排层
  backend/app/services/cloud_sync_service.py
    负责 manifest 比较、dirty note 扫描、冲突判断、同步状态更新

存储适配层
  backend/app/storage/provider.py
    定义统一对象存储接口
  backend/app/storage/aliyun_oss.py
    阿里云 OSS 实现
  backend/app/storage/local_mock.py
    本地测试实现
```

数据库模型和 schema 独立放在现有目录：

```text
backend/app/models/cloud_object.py
backend/app/models/sync_state.py
backend/app/schemas/cloud_sync.py
```

## 统一存储接口

存储 Provider 只处理对象存储的通用能力，不理解 note、manifest、冲突这些业务概念。

```python
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class StorageObjectMetadata:
    key: str
    size_bytes: int
    etag: str
    content_type: str
    last_modified: datetime | None = None
    storage_class: str | None = None


@dataclass(frozen=True)
class PresignedUrl:
    url: str
    expires_at: datetime
    method: str


class CloudObjectStorageProvider(Protocol):
    def get_bytes(self, key: str) -> bytes:
        ...

    def put_bytes(
        self,
        key: str,
        data: bytes,
        *,
        content_type: str,
        metadata: dict[str, str] | None = None,
    ) -> StorageObjectMetadata:
        ...

    def head_object(self, key: str) -> StorageObjectMetadata | None:
        ...

    def delete_object(self, key: str) -> None:
        ...

    def list_objects(self, prefix: str, *, limit: int = 1000) -> list[StorageObjectMetadata]:
        ...

    def create_upload_url(self, key: str, *, content_type: str, expires_seconds: int) -> PresignedUrl:
        ...

    def create_download_url(self, key: str, *, expires_seconds: int) -> PresignedUrl:
        ...
```

第一版同步笔记 JSON 和 manifest 可以只依赖 `get_bytes` / `put_bytes` / `head_object`。附件直传再接入 `create_upload_url` / `create_download_url`。

## Provider 实现

### AliyunOssStorageProvider

职责：

- 从配置读取 `bucket`、`endpoint`、`region`。
- 从环境变量读取 `ALIYUN_ACCESS_KEY_ID` 和 `ALIYUN_ACCESS_KEY_SECRET`。
- 把统一接口映射到 OSS SDK 调用。
- 把 OSS 异常转换成 AiMemo 内部异常，例如 `StorageNotFoundError`、`StorageAuthError`、`StorageUnavailableError`。

第一版只使用标准存储 Standard LRS，不在代码里自动转换 IA / Archive。

### LocalMockStorageProvider

职责：

- 把对象写入本地临时目录或测试目录。
- 用普通文件模拟 `put`、`get`、`head`、`delete`。
- 为单元测试提供确定性行为，不需要真实阿里云凭证。

这层非常重要：大部分同步逻辑都应该通过 mock provider 测试，真实 OSS 只做少量集成测试。

## 对象 Key 规范

统一由服务层生成 Key，Provider 不负责拼业务路径。

```text
users/{user_id}/sync/manifest.json
users/{user_id}/sync/notes/{note_id}.json
users/{user_id}/notes/{note_id}/attachments/{file_id}.{ext}
users/{user_id}/exports/{export_id}.{ext}
users/{user_id}/backups/{yyyy}/{mm}/{dd}/{backup_id}.sqlite.enc
tmp/uploads/{upload_id}/{part_name}
```

Key 生成规则集中放在一个模块中，例如：

```text
backend/app/services/cloud_key_service.py
```

这样可以避免不同 service 手写路径造成前缀不一致。

## 数据模型

### Note 同步字段

现有 `Note` 已经有 `content_hash`、`content_version`、`created_at`、`updated_at`，可以复用一部分。后续建议补充：

```text
notes
  cloud_revision: int
  local_revision: int
  last_synced_revision: int
  sync_status: str
  sync_conflict_id: str
  cloud_object_key: str
  last_synced_at: datetime
```

字段含义：

- `local_revision`: 本地每次修改、删除、恢复时递增。
- `cloud_revision`: 最后一次看到的远端 revision。
- `last_synced_revision`: 本地已成功同步到云端或从云端应用的 revision。
- `sync_status`: `synced`、`dirty`、`pulling`、`pushing`、`conflicted`、`error`。
- `sync_conflict_id`: 冲突记录 ID，第一版可以为空字符串，冲突时指向后续冲突表。
- `cloud_object_key`: 对应 `sync/notes/{note_id}.json`。

### SyncState

保存当前设备和当前用户命名空间的同步状态。

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
  created_at
  updated_at
```

### CloudObject

保存附件、导出、备份等对象元数据。

```text
cloud_objects
  id
  owner_user_id
  provider
  bucket
  region
  object_key
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

笔记 JSON 自身可以只靠 note 表记录，不一定写入 `cloud_objects`；附件、导出、备份建议写入。

## Manifest 结构

`manifest.json` 是拉取和上传的入口文件。它应该足够小，只保存同步判断所需信息。

```json
{
  "schema_version": 1,
  "user_id": "local-user",
  "global_revision": 42,
  "updated_at": "2026-06-13T10:30:00Z",
  "device_id": "desktop-a",
  "notes": {
    "1": {
      "revision": 7,
      "content_hash": "sha256:...",
      "updated_at": "2026-06-13T10:20:00Z",
      "deleted": false,
      "object_key": "users/local-user/sync/notes/1.json"
    }
  }
}
```

注意点：

- 第一版可以用单文件 manifest。
- 当笔记数量很大时，再演进为 manifest 分片。
- `global_revision` 只作为全局变化判断，不代替单条 note revision。
- `deleted=true` 表示远端软删除，本地不能因为对象还存在就恢复它。

## 笔记 JSON 结构

每条笔记保存为独立 JSON。附件只保存引用，不内嵌大文件。

```json
{
  "schema_version": 1,
  "id": 1,
  "title": "示例笔记",
  "title_source": "user",
  "content_markdown": "正文",
  "content_blocks": "",
  "content_format": "markdown",
  "content_version": 1,
  "content_hash": "sha256:...",
  "summary": "",
  "tags": ["cloud", "sync"],
  "status": "active",
  "deleted_at": null,
  "created_at": "2026-06-13T09:00:00Z",
  "updated_at": "2026-06-13T10:20:00Z",
  "revision": 7,
  "attachments": []
}
```

AI 生成字段如 `summary`、`tags` 可以同步。`processing_status`、`embedding_status` 这类本地任务状态不建议直接以远端为准，拉取笔记后应在本地按需要重新补建 embedding job。

## 服务层职责

### CloudStorageService

轻量包装 Provider，处理 JSON 编码、错误归一化和配置选择。

```text
get_json(key)
put_json(key, value)
get_bytes(key)
put_bytes(key, data)
head_object(key)
create_download_url(key)
```

### CloudSyncService

负责同步编排：

```text
pull_once(session)
push_once(session)
sync_once(session)  # 先 pull 后 push
mark_note_dirty(session, note)
get_sync_status(session)
resolve_conflict(session, conflict_id, action)
```

`note_service` 只需要在创建、修改、删除、恢复后调用 `mark_note_dirty` 或等价函数，不直接上传。

### CloudManifestService

负责 manifest 结构的读写和比较：

```text
load_remote_manifest()
load_local_manifest_snapshot(session)
compare_manifest(local, remote)
build_manifest_from_local(session)
merge_manifest_for_uploaded_notes(remote, uploaded_notes)
```

这层不直接修改 note 内容，只返回差异计划。

## 同步流程

### 本地编辑

```text
create/update/delete/restore note
  -> note.updated_at = now
  -> note.local_revision += 1
  -> note.sync_status = dirty
  -> 提交数据库事务
  -> 后台同步器稍后上传，或等待用户手动上传
```

第一版可以把 `mark_note_dirty` 放在 `note_service` 的事务里，避免笔记已改但同步标志没改。

### 手动拉取

```text
GET /api/cloud-sync/status
POST /api/cloud-sync/pull
  -> 读取远端 manifest
  -> 如果 global_revision 未变化，结束
  -> 比较每条 note revision
  -> 下载新增/更新的 note JSON
  -> 如果本地 note 为 dirty，标记冲突
  -> 否则应用远端内容
  -> 更新 SyncState
```

应用远端内容时要保持笔记列表排序依赖 `updated_at desc, id desc`。

### 手动上传

```text
POST /api/cloud-sync/push
  -> 扫描 sync_status=dirty 的 notes
  -> 读取远端 manifest 和 ETag
  -> 如果远端 manifest 已变化，先执行拉取/冲突检查
  -> 上传每条 dirty note JSON
  -> 更新并上传 manifest
  -> 标记本地 synced
  -> 更新 SyncState
```

如果远端 manifest 变化且出现冲突，默认停止上传冲突项；没有冲突的 dirty note 可以继续上传。

### 自动同步

自动同步由后台 job 或应用内轻量 scheduler 触发。第一版可以先实现手动同步，再接入周期配置。

建议触发条件：

- 应用启动后延迟拉取一次。
- 用户停止编辑 30 秒后上传 dirty notes。
- 每 15 分钟拉取一次远端 manifest。
- 应用退出前尝试短时上传，但不能长时间阻塞退出。

## API 设计

建议新增路由：

```text
GET  /api/cloud-sync/status
POST /api/cloud-sync/pull
POST /api/cloud-sync/push
POST /api/cloud-sync/sync
GET  /api/cloud-sync/config
PATCH /api/cloud-sync/config
GET  /api/cloud-sync/conflicts
POST /api/cloud-sync/conflicts/{conflict_id}/resolve
```

第一版可以只实现：

```text
GET  /api/cloud-sync/status
POST /api/cloud-sync/pull
POST /api/cloud-sync/push
POST /api/cloud-sync/sync
```

返回状态示例：

```json
{
  "enabled": true,
  "provider": "aliyun_oss",
  "user_id": "local-user",
  "last_pull_at": "2026-06-13T10:30:00Z",
  "last_push_at": "2026-06-13T10:40:00Z",
  "dirty_note_count": 2,
  "conflict_count": 0,
  "last_error": ""
}
```

## 配置

项目默认配置只保存非密钥信息：

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
      "signed_url_ttl_seconds": 900
    },
    "sync": {
      "enabled": false,
      "user_id": "local-user",
      "pull_interval_seconds": 900,
      "push_interval_seconds": 900,
      "pull_on_startup": true,
      "push_on_idle_seconds": 30,
      "conflict_policy": "keep_both"
    }
  }
}
```

密钥只从环境变量读取：

```text
ALIYUN_ACCESS_KEY_ID
ALIYUN_ACCESS_KEY_SECRET
```

真实 OSS 联调时，建议在 `.env` 或系统环境变量里提供：

```text
ALIYUN_ACCESS_KEY_ID=...
ALIYUN_ACCESS_KEY_SECRET=...
STORAGE_PROVIDER=aliyun_oss
STORAGE_ALIYUN_BUCKET=aimemo-dev
STORAGE_ALIYUN_ENDPOINT=https://oss-cn-hangzhou.aliyuncs.com
STORAGE_SYNC_USER_ID=local-user
```

其中 `ALIYUN_ACCESS_KEY_SECRET` 不应写入聊天记录、文档或 `config.json5`。`STORAGE_SYNC_USER_ID`
会参与对象 Key 前缀，例如 `users/local-user/sync/manifest.json`；如果 RAM 权限已经限制到某个
`users/{user_id}/*` 前缀，这里必须与授权前缀一致。

正式多用户版本应使用 STS 或服务端预签名 URL，避免客户端长期保存 AccessKey Secret。

## 错误和冲突

### 错误分类

- `auth_error`: 凭证缺失、权限不足、签名失败。
- `network_error`: 网络不可达、DNS、超时。
- `remote_not_found`: manifest 或 note JSON 不存在。
- `conflict`: 本地和远端都修改了同一条 note。
- `invalid_payload`: 远端 JSON schema 不符合预期。

### 冲突处理

第一版不自动合并正文。冲突时：

- 保留本地版本。
- 保存远端版本快照。
- note 标记为 `conflicted`。
- UI 提供“保留本地”“使用云端”“另存为副本”。

## 测试策略

优先写不依赖真实云的测试：

- Provider contract test：LocalMockProvider 满足统一接口。
- Manifest compare test：global revision 相同、单条 note 更新、新增、删除。
- Push test：只上传 dirty notes，上传后状态变 synced。
- Pull test：只下载 revision 变化的 note，未变化的不下载。
- Conflict test：本地 dirty 且远端 revision 更新时标记 conflicted。
- Serialization test：Note 和 note JSON 互相转换不丢关键字段。
- API test：手动 push/pull/status 返回稳定 schema。

真实 OSS 集成测试单独放到可选测试组，只有环境变量齐全时运行，避免本地开发默认产生云费用。

```text
pytest backend/tests/test_cloud_sync_service.py
pytest backend/tests/test_cloud_storage_provider.py
pytest backend/tests/test_cloud_sync_api.py
```

## 实现顺序

1. 新增配置读取和 storage provider 抽象。
2. 实现 LocalMockStorageProvider 和 provider contract tests。
3. 新增 sync_state / cloud_objects 模型。
4. 给 Note 增加同步字段，并让 note_service 修改后标记 dirty。
5. 实现 note JSON 序列化和 manifest 结构。
6. 实现手动 push。
7. 实现手动 pull。
8. 实现冲突检测。
9. 新增 API。
10. 接入前端同步设置面板。
11. 最后再接 AliyunOssStorageProvider 和真实 OSS 集成测试。

这个顺序可以先把最难出错的业务逻辑用本地 mock 跑通，再接真实云服务。
