# Ai 记

Ai 记是一个开源的个人知识库 Web 应用原型，目标是让用户记录日常笔记，并通过后续的 LangGraph Agent 和向量检索能力使用自己的个人知识库。

## 当前技术栈

- Backend: FastAPI + SQLModel + SQLite
- Frontend: Vite + React + TypeScript
- Agent: 预留 LangGraph Python 目录，后续接入
- Storage: 本地 `backend/data/ai_note.db`

## 开发启动

### 后端

```powershell
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .
python -m uvicorn app.main:app --reload
```

后端默认地址：`http://127.0.0.1:8000`

### 前端

```powershell
cd frontend
npm install
npm run dev
```

前端默认地址：`http://127.0.0.1:5173`

## 文档

项目文档按结构和主题组织在 `docs/` 目录中：

- [文档索引](./docs/README.md)
- [架构概览](./docs/architecture/overview.md)
- [本地开发](./docs/development.md)
