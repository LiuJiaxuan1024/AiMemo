# 前端说明

前端位于 `frontend/`，使用 Vite + React + TypeScript。

## 目录说明

```text
frontend/src/
  app/       全局壳层、模块路由和导航
  pages/     页面级组合组件
  features/  按功能拆分的前端模块
  services/  API 请求封装
  shared/    跨模块 UI 和 query client
  types/     前端类型定义
  App.tsx    挂载 AppShell
  main.tsx   React 入口
  styles.css 全局样式
```

## 当前界面

当前前端通过 `/app/*` 拆成三个模块：

- `/app/memo`：笔记记录、编辑、删除、恢复和永久删除。
- `/app/chat`：Memory Chat Graph 对话、流式输出和单轮 graph 调试。
- `/app/workshop/jobs`：后台任务和 Job Graph。
- `/app/workshop/memories`：长期记忆管理。
- `/app/workshop/voice`：语音工坊、声线管理、试听、文字声音设计和精灵语音模式开关。

`/app` 会自动进入 `/app/memo`，`/app/workshop` 会进入 `/app/workshop/jobs`。
后端会为任意 `/app/*` 路径回退到前端 `index.html`，因此刷新子页面不会 404。

## 相关文档

- [Workshop / Job Graph](./job-drawer.md)
- [Chat Window](./chat-window.md)
- [Chat View / Conversation Export 重构计划](./chat-view-export-refactor.md)
- [前端模块路由](./module-decoupling.md)
- [精灵助手](./elf-assistant.md)
- [精灵事件总线](./elf-event-bus.md)
- [原创精灵设计](./elf-character-design.md)
- [语音工坊第一版设计](../desktop/voice-workshop-design.md)

## API 地址

前端默认使用同源 API：

```text
/api/*
```

运行时配置读取：

```text
GET /api/config/runtime
```

当前运行时配置只暴露精灵语音模式等能力状态。Web 精灵组件由前端正常渲染；隐藏组件不再作为“关闭精灵”的配置语义。

产品入口由后端统一提供：

```text
http://127.0.0.1:8000/app
```

如需单独使用 Vite dev server，可以使用环境变量覆盖 API 地址：

```text
VITE_API_BASE_URL=http://127.0.0.1:8000
```

## 构建

```powershell
cd frontend
npm install
npm run build
```

构建产物生成在 `frontend/dist/`，不进入版本管理。
