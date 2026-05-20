# 架构概览

Ai 记是一个本地优先的开源个人知识库应用。核心能力是记录笔记、向量化检索、Memory Chat Graph 对话、长期记忆管理，以及外置桌面精灵交互。

## 当前技术栈

- Backend: FastAPI + SQLModel + SQLite
- Frontend: Vite + React + TypeScript + React Router + TanStack Query
- Agent: LangGraph Python + SQLite checkpoint
- RAG: sqlite-vec + DashScope embedding
- Desktop: Tauri 外置精灵
- Storage: 本地 SQLite，默认路径 `backend/data/ai_note.db`

## 产品入口

后端网关统一托管 API 和前端构建产物：

```text
http://127.0.0.1:8000/app
```

主要前端模块：

```text
/app/memo                 笔记记录、编辑、删除和恢复
/app/chat                 Memory Chat Graph 对话
/app/workshop/jobs        后台任务和 LangGraph 流程图
/app/workshop/memories    长期记忆管理
```

开发期可选 Vite 热更新入口：

```text
http://127.0.0.1:5173/app/
```

## 项目结构

```text
backend/
  app/
    agent/       LangGraph graph、模型、streaming、上下文构建
    api/         FastAPI 路由
    core/        配置、数据库、应用基础设施
    jobs/        本地任务队列、worker、reconciler
    models/      SQLModel 数据模型
    rag/         chunking、hash、vector store、search
    schemas/     API schema
    services/    业务服务层
    main.py
  tests/

frontend/
  src/
    app/         AppShell、模块路由
    pages/       页面级组合组件
    features/    notes/chat/jobs/memories/elf/graph
    services/    API 请求封装
    shared/      通用 UI 和 QueryClient
    types/
    App.tsx
    main.tsx
    styles.css

desktop/
  src-tauri/     Tauri 桌面精灵
  src/           桌面精灵前端

docs/
  architecture/
  backend/
  frontend/
  agent/
  api/
  desktop/
```

## 设计边界

业务数据和 Agent 运行状态分开管理：

- 用户笔记、chunk、对话、长期记忆和 job 状态保存在业务 SQLite。
- LangGraph checkpoint 用于保存 graph 执行状态、可恢复节点和对话运行上下文。
- Job 层负责“任务是否存在、是否完成、是否需要重试”。
- Graph checkpoint 层负责“任务执行到哪个节点、如何恢复执行”。
- API 层只处理 HTTP 入参和出参。
- Service 层封装业务逻辑。
- Agent 层通过明确服务或工具调用业务能力。

## 当前能力

已实现：

- 笔记创建、编辑、软删除、恢复和永久删除
- AI 自动标题、摘要、标签
- 笔记 chunk 分片、embedding 和 sqlite-vec 向量索引
- 向量相似度检索 API
- 本地 jobs 任务队列、启动补偿和周期 reconcile
- LangGraph SQLite checkpoint
- Memory Chat Graph 流式输出
- L0-L4 上下文金字塔 worker
- L2 对话滚动摘要
- L4 长期记忆抽取、归并、启用、停用和删除
- 前端 Markdown 渲染
- 前端模块路由解耦
- Workshop 后台任务和记忆管理
- 桌面外置精灵事件和聊天入口

暂未实现：

- 用户登录
- 云端同步
- 记忆版本历史
- 对话编辑与状态树 UI
- 精灵系统自动化能力

## 流程图

核心数据流、job 状态和 graph 状态见 [流程图](./flows.md)。
