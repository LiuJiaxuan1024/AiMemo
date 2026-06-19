# 架构概览

Memo Elf / AiMemo 是一个本地优先的桌面记忆精灵项目。当前产品已经从单一 AI 笔记应用演进为“桌面精灵 + 记忆系统 + 本地 Agent 工具”的组合：Memo Elf 负责陪伴、对话和技能入口；AiMemo 是第一项记忆能力，负责笔记、长期记忆、检索和 Memory Chat Graph。

## 当前技术栈

- Backend: FastAPI + SQLModel + SQLite
- Frontend: Vite + React + TypeScript + React Router + TanStack Query
- Agent: LangGraph Python + SQLite checkpoint
- RAG: sqlite-vec + DashScope embedding
- Desktop: Tauri 外置精灵
- Local Operator: 受控文件读写、短时命令执行、后台服务任务管理
- Storage: 本地 SQLite + 可选阿里云 OSS 同步，SQLite 默认路径 `backend/data/ai_note.db`

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
/app/workshop/voice       语音工坊和声线管理
/app/workshop/sync        阿里云 OSS 云同步工坊
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
    local_operator/ 本地文件 / 命令工具、权限策略和审计
    models/      SQLModel 数据模型
    providers/   外部 provider 适配，例如 web_search
    rag/         chunking、hash、vector store、search
    schemas/     API schema
    services/    业务服务层
    storage/     本地 mock / 阿里云 OSS 对象存储 Provider
    main.py
  tests/

frontend/
  src/
    app/         AppShell、模块路由
    pages/       页面级组合组件
    features/    notes/chat/chat_view/cloud_sync/jobs/memories/elf/graph/voice
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
- ReAct 风格工具循环，工具结果以 ToolMessage 回灌给 agent
- 结构化 `request_user_input` 中断，可在聊天窗口或桌面精灵里渲染选项卡
- L2 对话滚动摘要
- L4 长期记忆抽取、归并、启用、停用和删除
- Local Operator 文件读取、整文件写入、短时命令执行
- 后台服务型命令启动、输出轮询、终止和任务抽屉管理
- Graph 调试工作台：Mermaid 图、checkpoint history、state diff、节点 state 查看
- 对话 HTML 导出：消息树、片段追问、Markdown、KaTeX 数学公式、Mermaid 和代码高亮
- Web Search 工具：Tavily / 阿里云百炼 provider、缓存、限额、审计和 fetch 核验
- 阿里云 OSS 云同步：notes / conversations / memories / config / knowledge 分域 push / pull / sync
- 云同步冲突列表和加密备份入口
- 语音工坊：DashScope ASR / TTS / Voice Design、声线管理、试听和默认声线
- 前端 Markdown 渲染
- 前端模块路由解耦
- Workshop 后台任务、记忆、语音和同步管理
- 桌面外置精灵事件和聊天入口

仍在计划或需要加固：

- 用户登录
- 记忆版本历史
- 长期记忆候选确认、superseded/conflicted 生命周期和独立证据表
- 对话编辑与状态树 UI
- 精灵系统级自动化能力
- 更细粒度的 edit/patch 工具、diff preview、rollback 和 Local Operator 高风险审批闭环
- 固定 memory/retrieval eval set

更详细的模块状态见 [项目状态矩阵](../status.md)。

## 流程图

核心数据流、job 状态和 graph 状态见 [流程图](./flows.md)。
