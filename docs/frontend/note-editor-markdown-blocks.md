# 笔记编辑器：Markdown + Block JSON 双存方案

## 背景

AiMemo 当前的笔记正文以 Markdown 字符串为主。Markdown 对 RAG、摘要、导出和 API
兼容非常友好，但不适合作为飞书 / Notion 式块编辑器的完整编辑状态。

因此后续笔记内容采用双存：

- `content_markdown`：语义文本主副本，用于检索、摘要、embedding、导出和旧客户端兼容。
- `content_blocks`：块编辑器状态，用于恢复飞书式块编辑体验。

兼容字段 `content` 暂时保留，并始终等于 `content_markdown`。

## 数据模型

`notes` 表新增：

- `content_markdown TEXT`
- `content_blocks TEXT`：JSON 字符串，当前先允许为空。
- `content_format VARCHAR(24)`：`markdown` 或 `blocknote`
- `content_version INTEGER`

旧数据迁移时：

- `content_markdown = content`
- `content_blocks = ''`
- `content_format = 'markdown'`
- `content_version = 1`

## API 约定

创建 / 更新笔记请求支持：

- `content`：旧字段，等价于 Markdown。
- `content_markdown`：新 Markdown 字段。
- `content_blocks`：块 JSON 字符串。
- `content_format`：默认 `markdown`。

后端处理规则：

1. 优先使用 `content_markdown`，其次使用 `content`。
2. 后端永远用 Markdown 计算 `content_hash`。
3. metadata、chunk、embedding、RAG 都只消费 Markdown。
4. 保存 BlockNote 时，前端同时提交 blocks 和导出的 Markdown。

## 前端路线

当前版本：

- 使用 BlockNote 提供飞书 / Notion 式块编辑体验。
- 输入 `## `、`- `、`> ` 等 Markdown shortcut 会即时转换为标题、列表、引用。
- 打开旧 Markdown 笔记时，如果没有 `content_blocks`，前端会将 Markdown 转为 blocks。
- 保存时提交：
   - `content_blocks`: BlockNote document JSON
   - `content_markdown`: `blocksToMarkdownLossy(blocks)` 的导出结果
   - `content_format`: `blocknote`

注意：Markdown 是面向 RAG 和导出的语义副本，复杂块在 `blocksToMarkdownLossy` 导出时可能有损；
再次编辑时优先使用 `content_blocks` 恢复编辑器状态。

## 为什么不只存 blocks

只存 blocks 会让 RAG、搜索、导出、外部 API 和调试链路都依赖编辑器格式。双存能让：

- 编辑器体验继续进化。
- AI 处理链路保持稳定。
- 旧客户端和旧数据平滑兼容。
