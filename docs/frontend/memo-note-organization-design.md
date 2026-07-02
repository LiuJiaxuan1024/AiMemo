# Memo 笔记轻量组织层设计

更新时间：2026-06-27

本文记录 AiMemo 主页笔记模块的下一阶段设计。方向采用“轻量组织层”：补齐分类、标签管理、置顶/收藏和筛选视图，同时保持本地优先、AI 辅助整理和 OSS 云同步兼容。

## 背景

当前 Memo / Notes 已具备笔记创建、编辑、软删除、恢复、标题、摘要、标签、搜索、排序、AI metadata 处理和 embedding。它能支撑少量笔记的快速记录，但当笔记数量增长后，用户缺少稳定的组织入口：

- 无法按分类或文件夹浏览。
- 标签只是字符串数组，没有统一管理、重命名、合并能力。
- 左侧栏只有列表、搜索和排序，缺少常用视图。
- 置顶、收藏、归档、未分类等轻量管理动作缺失。
- OSS 里已有旧版 note JSON，新增组织字段必须兼容旧云端数据。

市场调研中，Evernote、OneNote、Apple Notes、Joplin 这类传统笔记软件都把“文件夹/笔记本 + 标签 + 搜索 + 回收站”作为基础组织能力；Notion/Obsidian 的属性、双链、图谱价值很高，但第一阶段不适合作为 Memo 首页的默认复杂度。

## 目标

- 让用户可以按“分类/标签/状态/快速视图”管理笔记。
- 保持主页笔记的快速记录体验，不把它做成完整 Notion 数据库。
- 标签从轻量字符串逐步演进为可管理实体，但兼容现有 `notes.tags`。
- 分类/收藏/置顶等组织信息进入 OSS 同步 payload，旧 payload 缺字段时正常拉取。
- 为后续 AI 自动分类、批量整理、知识库关联留下边界。

## 非目标

- 第一阶段不做自定义属性数据库、关系字段、复杂视图保存。
- 第一阶段不做 Obsidian 式双链图谱。
- 第一阶段不做多人协作、共享分类、权限系统。
- 第一阶段不要求云端直接支持分类查询；OSS 仍只是同步副本。
- 第一阶段不迁移 note id 或云端 object key。

## 产品模型

### 组织对象

| 对象 | 说明 | 第一阶段策略 |
| --- | --- | --- |
| 分类 | 类似轻量文件夹，一条笔记最多属于一个分类 | 新增一张本地表，笔记保存 `category_id` |
| 标签 | 可多选、可搜索、可重命名的主题标记 | 继续兼容 `notes.tags`，逐步增加管理 API |
| 快速视图 | 全部、未分类、收藏、置顶、最近删除、处理中 | 前端组合已有字段和新增字段 |
| 收藏 | 用户主动标记的重要笔记 | 新增 `is_favorite` |
| 置顶 | 列表排序优先展示 | 新增 `pinned_at` |
| 归档 | 暂时移出默认列表但不删除 | 第二阶段再做，避免状态语义过早膨胀 |

分类和标签的关系应保持克制：分类解决“放在哪”，标签解决“关于什么”。一条笔记只属于一个分类，但可以有多个标签。

### 默认视图

左侧栏建议分为四块：

1. 快速入口：全部笔记、未分类、收藏、置顶、最近删除。
2. 分类：用户创建的分类列表，显示每个分类下 active 笔记数量。
3. 标签：常用标签列表，支持展开全部。
4. 列表工具：搜索、排序、AI 处理状态筛选。

笔记列表进入某个分类或标签后，仍保留搜索和排序。搜索应在当前视图内生效，而不是每次回到全局。

## 数据设计

### Notes 增量字段

在 `Note` 上追加字段，不修改现有主键和内容字段：

| 字段 | 类型 | 默认值 | 说明 |
| --- | --- | --- | --- |
| `category_id` | `int | None` | `None` | 所属分类；空表示未分类 |
| `is_favorite` | `bool` | `False` | 收藏 |
| `pinned_at` | `datetime | None` | `None` | 非空表示置顶，按时间排序 |
| `archived_at` | `datetime | None` | `None` | 第二阶段预留；第一阶段可不暴露 |

现有 `status` 继续只表达 `active/deleted`。不要把分类、收藏、归档塞进 `status`，否则会破坏最近删除和同步冲突语义。

### 分类表

新增 `NoteCategory`：

| 字段 | 说明 |
| --- | --- |
| `id` | 本地整数 id |
| `name` | 分类名，用户可修改 |
| `description` | 可选描述，第一阶段 UI 可不展示 |
| `sort_order` | 用户自定义排序 |
| `color` | 可选颜色 token |
| `status` | `active/deleted`，用于软删除分类 |
| `cloud_revision/local_revision/sync_status/...` | 复用同步元数据模式，第二阶段可纳入独立 domain |
| `created_at/updated_at/deleted_at` | 生命周期 |

第一阶段分类删除采用“移出分类”：删除分类时将其笔记 `category_id` 置空，不级联删除笔记。

### 标签策略

短期继续使用 `notes.tags` 作为事实字段，原因是当前 AI metadata、note list、OSS payload 都已经依赖它。第一阶段先补：

- 标签筛选视图。
- 标签重命名：遍历 active/deleted notes 中的 tag 字符串并替换。
- 标签合并：把多个旧 tag 替换成一个新 tag。
- 标签删除：从所有 notes 中移除该 tag。

如果后续标签操作变多，再引入 `NoteTag` / `NoteTagLink` 正规化表。引入时仍保持 `notes.tags` 作为同步和检索的兼容快照，避免一次性迁移风险。

## API 设计

### Notes 查询

扩展 `GET /api/notes` 查询参数：

| 参数 | 说明 |
| --- | --- |
| `status` | 现有 `active/deleted` |
| `category_id` | 分类过滤；特殊值 `uncategorized` 可表示未分类 |
| `tag` | 标签过滤 |
| `favorite` | 只看收藏 |
| `pinned` | 只看置顶 |
| `processing_status` | AI 处理状态 |
| `q` | 服务端搜索，第二阶段可接管当前前端本地搜索 |
| `sort` | `updated/created/title/pinned` |

第一阶段可以先保持前端本地过滤，但后端 API 需要为大规模笔记预留服务端过滤能力。

### Notes 更新

扩展 `PATCH /api/notes/{id}`：

- `category_id`
- `tags`
- `is_favorite`
- `pinned`

内容更新和组织信息更新应区分处理：只改分类、标签、收藏、置顶时，不应重建 note chunks 和 embedding。标签变化是否触发 embedding 重建需要谨慎：第一阶段不触发，只标记 note dirty 进入 OSS 同步。

### 分类 API

新增：

```text
GET    /api/note-categories
POST   /api/note-categories
PATCH  /api/note-categories/{id}
DELETE /api/note-categories/{id}
```

删除分类默认将关联笔记变为未分类。

### 标签 API

新增轻量管理接口：

```text
GET  /api/note-tags
POST /api/note-tags/rename
POST /api/note-tags/merge
POST /api/note-tags/delete
```

这些接口第一阶段可以直接基于 `notes.tags` 聚合生成，不必先建标签表。

## OSS 同步兼容

### 现状

当前 notes 同步使用：

```text
users/{user_id}/sync/domains/notes_manifest.json
users/{user_id}/sync/notes/{note_id}.json
```

同时仍兼容旧 manifest：

```text
users/{user_id}/sync/manifest.json
```

现有 note payload 已包含 `title`、`content_markdown`、`content_blocks`、`summary`、`tags`、`status`、`created_at`、`updated_at`、`revision`、`object_key` 等字段。

### 兼容原则

1. **只追加字段，不重命名旧字段。**
2. **旧 payload 缺字段时使用默认值。**
3. **manifest 结构不因分类功能变化而破坏。**
4. **note object key 继续使用原路径。**
5. **分类对象如果单独同步，使用新的 domain，不塞进 notes manifest。**

### Note Payload 增量字段

上传 note JSON 时追加：

```json
{
  "category_id": 3,
  "category_name": "项目",
  "is_favorite": true,
  "pinned_at": "2026-06-27T10:00:00Z",
  "archived_at": null,
  "organization_schema_version": 1
}
```

拉取旧 payload 时：

- `category_id` 缺失：本地置为 `None`。
- `category_name` 缺失：不创建分类。
- `is_favorite` 缺失：`False`。
- `pinned_at` 缺失：`None`。
- `archived_at` 缺失：`None`。

`category_name` 主要用于跨设备恢复时提高容错：如果目标设备没有对应 `category_id`，可以尝试按名称匹配分类。第一阶段可以只保存字段，不做复杂匹配；第二阶段再完善。

### 分类同步策略

分类有两种选择：

| 方案 | 做法 | 优点 | 风险 |
| --- | --- | --- | --- |
| A1 嵌入 note payload | 每条 note 保存 `category_id/category_name` | 最少改动，能快速同步笔记所属分类 | 空分类不会同步；分类重命名依赖每条 note 更新 |
| A2 新增 `note_categories` domain | 分类独立 JSON 和 manifest | 结构清晰，支持空分类和排序 | 同步复杂度略高 |

推荐阶段性采用：

- 第一阶段用 A1，保证旧 OSS 数据完全兼容，减少同步链路改动。
- 第二阶段增加 `note_categories` domain，让分类自身也可同步。

这样可以先把用户最直接的分类体验做出来，同时不急着扩展云同步 domain。

### 冲突处理

组织字段属于用户编辑事实，冲突策略和 note 内容一致：

- 如果远端 revision 更新而本地 note dirty，继续进入冲突，不静默覆盖。
- `keep_both` 时，两份 note 都应保留各自分类、标签、收藏、置顶状态。
- 只改组织字段也要 `mark_note_dirty`，确保能被上传。

### 迁移策略

本地数据库迁移：

- 新增字段均有默认值或允许为空。
- 旧笔记自动进入“未分类”。
- 旧 tags 不迁移，保持原字符串存储。

云端迁移：

- 不需要批量改 OSS 旧对象。
- 用户下一次编辑或同步 dirty note 时，自然上传新字段。
- pull 旧对象时通过默认值兼容。
- legacy manifest 继续由现有逻辑写出，字段变化不影响旧 manifest 的 `notes` 条目。

## 前端设计

### 侧边栏

当前 `NoteSidebar` 负责品牌、统计、状态 tab、搜索、排序和 note list。建议演进为：

- `NoteNavigation`：快速视图、分类列表、标签列表。
- `NoteListPanel`：搜索、排序、处理状态筛选、笔记列表。
- `MemoPage`：持有当前 filter state。

这样可以避免单个 sidebar 继续膨胀。

### 笔记详情

`NoteDetail` 增加组织操作区：

- 分类选择器。
- 标签编辑器。
- 收藏按钮。
- 置顶按钮。

这些操作应是轻量即时保存，不要求用户点击“保存正文”。正文编辑仍保留现有编辑流程。

### 空状态

需要明确几个空状态：

- 全部笔记为空：提示写第一条笔记。
- 当前分类为空：提示移动笔记到此分类或新建笔记。
- 当前标签为空：提示标签已无关联笔记。
- 未分类为空：说明所有笔记都已分类。

## 阶段计划

### Phase 1：最小可用组织层

- 后端为 notes 增加 `category_id`、`is_favorite`、`pinned_at`。
- 新增分类模型和分类 API。
- `PATCH /api/notes/{id}` 支持更新分类、标签、收藏、置顶。
- 前端增加快速视图、分类列表、标签筛选、收藏/置顶按钮。
- OSS note payload 追加组织字段，pull 旧对象使用默认值。
- 覆盖 note service、cloud sync、MemoPage 基础测试。

### Phase 2：管理增强

- 标签重命名、合并、删除。
- 分类排序、重命名、删除确认。
- 批量选择笔记，批量移动分类/加标签/删除。
- 服务端 notes 过滤和分页。
- 可选新增 `note_categories` sync domain。

### Phase 3：AI 辅助整理

- AI 推荐分类和标签，但需要用户确认。
- 未分类笔记整理队列。
- 基于摘要/内容相似度推荐合并标签。
- 在 Memory Chat 中支持“按分类/标签检索笔记”。

## 测试重点

- 旧 note 创建、更新、删除、恢复行为不变。
- 只改组织字段不触发 metadata/embedding 重建。
- 组织字段变更会标记 note dirty。
- pull 旧 OSS note payload 不报错，默认未分类、未收藏、未置顶。
- push 新 note payload 包含组织字段，但 legacy manifest 仍可读取。
- 分类删除不会删除笔记。
- 标签重命名/合并不会产生重复 tag。
- 前端在全部、分类、标签、收藏、置顶、最近删除间切换时 selected note 状态正确。

## 待确认问题

1. 分类是否允许层级结构。建议第一阶段不支持层级，只做平铺分类。
2. 收藏和置顶是否都需要。建议都保留：收藏表达重要，置顶表达列表优先。
3. 归档是否进入第一阶段。建议暂缓，避免和最近删除、知识库收纳语义冲突。
4. 标签是否立即正规化成表。建议暂缓，先用管理 API 包住现有字符串字段。
