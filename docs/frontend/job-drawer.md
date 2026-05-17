# Job Drawer

Job Drawer 是右侧悬浮的精灵工坊，用于调试 Ai 记的 job、graph 和 checkpoint 执行状态，并管理 L4 长期记忆。

## 交互

- 鼠标悬停右侧 `精灵` 按钮时展开。
- 点击按钮或面板内 `固定` 可以保持展开。
- `任务` tab 会轮询后台任务列表。
- 选择某个 job 后，会读取对应 LangGraph 流程图。
- `记忆` tab 会读取长期记忆列表，支持筛选、编辑、停用和重新启用。

## 当前显示内容

- job 列表
- job 状态
- job attempts / max_attempts
- job payload
- job graph Mermaid 图
- checkpoint 中的下一步节点
- 生效 / 停用长期记忆
- 长期记忆 category / content / summary / importance / confidence / status

## 文件结构

```text
frontend/src/features/jobs/
  JobDrawer.tsx
  JobList.tsx
  JobDetail.tsx
  JobGraphView.tsx
  jobsApi.ts
  types.ts

frontend/src/features/memories/
  MemoryPanel.tsx
  memoriesApi.ts
  types.ts
```

## 记忆管理

`记忆` tab 使用 Memories API：

```text
GET /api/memories
PATCH /api/memories/{id}
DELETE /api/memories/{id}
```

当前支持：

```text
生效 / 停用切换
category 筛选
编辑 content / summary / category / importance / confidence / status
停用生效记忆
重新启用已停用记忆
```

停用后的记忆底层状态为 `archived`，不会进入 Memory Chat Graph 的 L4 worker；
重新启用会把状态改回 `active`。

## Mermaid 渲染

前端使用动态 `import("mermaid")` 渲染流程图，只有打开 graph 时才加载 Mermaid，避免首屏加载过重。

后端负责返回 LangGraph 原生 Mermaid，并追加当前节点高亮样式。
