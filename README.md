# Ai 记 / AiMemo

Ai 记是一个开源的个人记忆笔记应用。`Ai` 既表示 AI，也来自中文“爱”，表达“热爱记录”的含义。

AiMemo aims to be a personal memory notebook powered by AI, built for people who love recording life.

## 项目定位

Ai 记不是传统笔记软件，也不是单纯的聊天机器人。它的目标是：

```text
记录日常笔记
将笔记向量化存储
基于个人知识库进行对话和检索
通过 LangGraph 管理可恢复的 AI 工作流
用一个精灵助手把后台任务和记忆状态可视化
```

当前项目仍处于早期开发阶段，重点是把核心功能、架构和本地体验跑通。

## 当前能力

已实现的核心能力：

```text
笔记创建与本地 SQLite 存储
笔记标题 / 摘要 / 标签自动整理
笔记 chunk 分片与 embedding 入库
基于 sqlite-vec 的本地向量检索
Memory Chat Graph 对话
流式回答输出
上下文金字塔构建
长期记忆提取、启用、停用和编辑
LangGraph checkpoint 持久化
本地 job 队列与启动恢复
任务 graph 可视化
右下角 Live2D 精灵助手
前端 Markdown 渲染和基础调试面板
```

## 技术栈

Backend:

```text
Python
FastAPI
SQLModel
SQLite
sqlite-vec
LangGraph
LangGraph checkpoint sqlite
DashScope / OpenAI-compatible API
```

Frontend:

```text
React 19
Vite
TypeScript
TanStack Query
Mermaid
react-markdown
OhMyLive2D
lucide-react
```

## 项目结构

```text
backend/
  app/
    agent/       LangGraph graph、模型、streaming、上下文构建
    api/         FastAPI 路由
    jobs/        本地任务队列、worker、reconciler
    models/      SQLModel 数据模型
    rag/         chunking、hash、vector store、search
    services/    业务服务层
  tests/         后端测试

frontend/
  src/
    features/
      chat/      对话窗口、stream、graph 调试
      elf/       精灵助手
      jobs/      精灵工坊和任务可视化
      memories/  长期记忆管理
      notes/     笔记列表、编辑器、详情
    shared/      通用 UI 和 QueryClient

docs/
  agent/         Agent / graph 设计文档
  api/           API 文档
  architecture/  架构与流程图
  backend/       后端模块说明
  frontend/      前端模块与体验优化记录
```

## 本地开发

## 快速开始

### 1. 克隆项目

```powershell
git clone https://github.com/LiuJiaxuan1024/AiMemo.git
cd AiMemo
```

### 2. 准备环境变量

复制示例配置，并填入自己的阿里百炼 API Key：

```powershell
Copy-Item .env.example .env
notepad .env
```

Linux / macOS：

```bash
cp .env.example .env
nano .env
```

至少需要配置：

```text
DASHSCOPE_API_KEY=你的百炼 API Key
```

### 3. 启动后端

推荐使用脚本启动。脚本会自动创建 `backend/.venv` 并安装后端依赖：

Windows PowerShell：

```powershell
.\scripts\start-backend.ps1
```

Linux / macOS：

```bash
chmod +x scripts/start-backend.sh scripts/start-frontend.sh
./scripts/start-backend.sh
```

后端默认地址：

```text
http://127.0.0.1:8000
```

API 文档：

```text
http://127.0.0.1:8000/docs
```

### 4. 启动前端

另开一个终端窗口。

Windows PowerShell：

```powershell
.\scripts\start-frontend.ps1
```

Linux / macOS：

```bash
./scripts/start-frontend.sh
```

前端默认地址：

```text
http://127.0.0.1:5173
```

### 5. 验证

打开前端后，可以先创建一条笔记。如果后端和模型配置正常，稍等片刻后会看到：

```text
笔记摘要 / 标签生成
笔记进入向量化任务
右下角精灵显示后台任务状态
对话窗口可以基于笔记回答问题
```

更多启动细节和常见问题见：`docs/setup.md`。

## 手动启动

如果不使用脚本，也可以按下面方式手动启动。

### 后端

建议使用 Python 3.12。项目依赖声明为 `>=3.11,<3.14`，但当前本地开发主要使用 3.12 虚拟环境。

Windows PowerShell：

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

Linux / macOS：

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev]"
python -m uvicorn app.main:app --reload --host 127.0.0.1 --port 8000
```

后端默认地址：

```text
http://127.0.0.1:8000
```

### 前端

```powershell
cd frontend
npm install
npm run dev -- --host 127.0.0.1
```

前端默认地址：

```text
http://127.0.0.1:5173
```

## 环境变量说明

复制示例配置：

```powershell
Copy-Item .env.example .env
```

主要配置：

```text
DASHSCOPE_API_KEY=
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_DIMENSIONS=1024

DATABASE_URL=sqlite:///./data/ai_note.db
LANGGRAPH_CHECKPOINT_PATH=./data/langgraph_checkpoints.db
JOB_WORKER_ENABLED=true
```

当前默认使用阿里百炼 DashScope 的 OpenAI-compatible API。用户需要自行提供 API Key。

`.env` 可以放在仓库根目录；后端从 `backend/` 目录启动时也会读取根目录配置。

## 测试与构建

后端测试：

```powershell
cd backend
.\.venv\Scripts\Activate.ps1
pytest
```

前端构建：

```powershell
cd frontend
npm run build
```

## 文档入口

推荐从这里开始：

```text
docs/README.md
docs/setup.md
docs/architecture/overview.md
docs/architecture/flows.md
docs/agent/memory-chat-graph.md
docs/agent/memory-chat-graph-design.md
docs/backend/jobs.md
docs/backend/vector-storage.md
docs/frontend/elf-assistant.md
docs/frontend/ui-optimization-report.md
```

## 注意事项

```text
当前仍是早期开发项目，接口和数据结构可能频繁变化。
本地数据库、checkpoint、日志、虚拟环境和 node_modules 不会提交到仓库。
Live2D 当前使用远程示例模型，仅用于开发验证。
开源发布前需要替换为版权明确、允许分发的本地模型资源。
```

## License

MIT
