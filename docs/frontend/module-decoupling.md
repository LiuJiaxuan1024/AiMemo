# 前端模块路由

本文档描述 AiMemo 当前前端模块结构。历史上的“模块解耦计划”已经落地，当前代码使用 `react-router-dom` 管理 `/app/*` 路由。

## 产品入口

统一入口由后端网关提供：

```text
http://127.0.0.1:8000/app
```

开发期 Vite 热更新入口：

```text
http://127.0.0.1:5173/app/
```

## 路由

```text
/app
  -> redirect /app/memo

/app/memo
  -> MemoPage

/app/chat
  -> ChatPage

/app/workshop
  -> redirect /app/workshop/jobs

/app/workshop/jobs
  -> WorkshopJobsPage

/app/workshop/memories
  -> WorkshopMemoriesPage

/app/*
  -> redirect /app/memo
```

后端对任意 `/app/*` 路径回退到 `frontend/dist/index.html`，因此刷新子页面不会 404。

## 模块边界

### Memo

路径：

```text
/app/memo
```

职责：

- 记录笔记
- 查看笔记列表
- 查看笔记详情
- 编辑笔记
- 删除、恢复、永久删除笔记
- 展示笔记处理状态

不负责：

- 聊天
- 长期记忆列表
- Job Graph 调试
- 精灵配置

### Chat

路径：

```text
/app/chat
```

职责：

- 会话列表
- 消息列表
- 发送消息
- 流式输出
- 每条 assistant 消息的 graph 查看
- 上下文金字塔和检索证据调试面板

不负责：

- 创建或编辑笔记
- 管理全部后台 job
- 管理长期记忆列表
- 精灵配置

### Workshop

路径：

```text
/app/workshop/jobs
/app/workshop/memories
```

职责：

- 后台任务列表
- Job Graph 可视化
- 长期记忆管理
- 记忆详情追踪
- 后续精灵配置入口

不负责：

- 笔记正文编辑
- 正式聊天主流程

## 目录结构

```text
frontend/src/
  app/
    AppShell.tsx
    routes.ts
  pages/
    memo/
      MemoPage.tsx
    chat/
      ChatPage.tsx
    workshop/
      WorkshopPage.tsx
      WorkshopJobsPage.tsx
      WorkshopMemoriesPage.tsx
  features/
    notes/
    chat/
    jobs/
    memories/
    elf/
    graph/
  services/
  shared/
  types/
```

说明：

- `app/`: 全局壳、模块导航和路由辅助。
- `pages/`: 页面级组合组件，只负责组装 feature。
- `features/`: 高内聚业务功能组件。
- `shared/`: 跨模块 UI 和 query client。

## AppShell

`AppShell` 负责：

- 顶部模块导航
- 当前路径高亮
- 承载 `react-router` 的 `Outlet`

`App.tsx` 负责创建 router，并对页面组件做 `React.lazy`，避免首屏加载所有模块。

## 性能策略

当前前端已经做了两层懒加载：

- 页面级 lazy route：Memo、Chat、Workshop 子页面按路由加载。
- Graph 级 lazy：Chat Graph Panel、Job Graph View 和 Mermaid 渲染器按需加载。

Mermaid 本身仍然会生成较大的异步 chunk，这是库体积决定的。由于它只在打开 Graph 图时加载，不影响普通笔记和聊天首屏。

## 后端 API 边界

本次只拆前端模块边界。后端继续使用当前 API：

```text
/api/notes
/api/conversations
/api/jobs
/api/memories
/api/elf
```

后端的 graph、job、memory 仍然高度协作，暂不拆成多个服务。

## 验证

```powershell
cd frontend
npm run build
```

```powershell
backend\.venv\Scripts\python.exe -m pytest backend\tests\test_app_routes.py
```

手动验证：

```text
http://127.0.0.1:8000/app
http://127.0.0.1:8000/app/memo
http://127.0.0.1:8000/app/chat
http://127.0.0.1:8000/app/workshop/jobs
http://127.0.0.1:8000/app/workshop/memories
```
