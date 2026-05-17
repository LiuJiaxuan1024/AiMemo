# 架构概览

Ai 记当前是一个本地优先的开源 Web 应用原型，目标是先跑通笔记存储、后端 API、前端交互，并为后续 LangGraph Agent 和向量检索预留清晰边界。

## 当前技术栈

- Backend: FastAPI + SQLModel + SQLite
- Frontend: Vite + React + TypeScript
- Agent: LangGraph Python + SQLite checkpoint
- Storage: 本地 SQLite，默认路径 `backend/data/ai_note.db`

## 项目结构

```text
backend/
  app/
    api/
    agent/
    core/
    models/
    schemas/
    services/
    main.py
  data/
  pyproject.toml

frontend/
  src/
    services/
    types/
    App.tsx
    main.tsx
    styles.css
  package.json

docs/
  architecture/
  backend/
  frontend/
  agent/
  api/
```

## 设计边界

业务数据和 Agent 运行状态需要分开管理：

- 用户长期笔记以业务数据库和后续向量索引作为主存储。
- LangGraph checkpoint 优先用于保存会话状态、图执行过程和可恢复工作流。
- API 层只处理 HTTP 入参和出参。
- Service 层封装业务逻辑。
- Agent 层通过明确工具调用业务能力。

## 流程图

核心数据流、job 状态和 graph 状态见 [流程图](./flows.md)。

## 当前功能边界

当前已实现：

- 本地 Web 服务
- 笔记创建
- 笔记列表
- 笔记详情
- SQLite 持久化
- 本地 jobs 任务队列
- LangGraph SQLite checkpoint
- AI 自动标题、摘要、标签
- 笔记 chunk 分片
- DashScope embedding
- sqlite-vec 本地向量索引
- 向量相似度检索 API
- 对话和消息业务表
- Memory Chat Graph MVP
- job 启动补偿和周期 reconcile
- Job Drawer 后台任务可视化

当前未实现：

- 用户登录
- 前端聊天入口
- query rewrite / retrieval grading
- 长期记忆抽取
- 云端同步
