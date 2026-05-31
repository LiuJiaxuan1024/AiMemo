# Memo Elf / AiMemo

Memo Elf 是一个本地优先的开源桌面记忆精灵。它会以桌面悬浮精灵的形式陪伴用户，连接本地 AI agent、个人记忆库和未来更多电脑操作能力。

AiMemo / Ai 记是 Memo Elf 的第一个核心能力：记录笔记、沉淀记忆、检索个人知识，并让 AI 基于这些记忆和用户对话。

`Ai` 既表示 AI，也来自中文“爱”，表达“热爱记录”的含义。

Memo Elf aims to be a personal desktop memory companion. AiMemo is its first built-in memory skill.

## 项目定位

这个项目已经从单一的 AI 笔记应用，演进为“桌面精灵 + 记忆系统 + Agent Skills”的本地智能体实验。

当前主线是：

```text
Memo Elf
  桌面精灵本体。
  负责陪伴、对话、气泡反馈、表情切换和未来技能入口。

AiMemo Memory Skill
  Memo Elf 的第一个内置能力。
  负责笔记、长期记忆、向量检索、Memory Chat Graph 和记忆管理。

Local Operator
  面向未来的本地电脑操作能力。
  当前已接入主对话工具循环，支持受控 read / write / exec，以及后台服务任务管理。

Voice Studio
  精灵语音能力与声线工坊。
  当前采用阿里百炼 / DashScope 远程 ASR、TTS 和 Voice Design，不再默认下载或部署本地语音模型。
```

换句话说：

```text
过去：AiMemo with Elf
现在：Memo Elf with AiMemo Memory Skill
未来：Memo Elf with Memory / File / Browser / Automation Skills
```

当前项目仍处于早期开发阶段，重点是把本地后端、桌面精灵、记忆系统、LangGraph 工作流和基础工具调用跑通。

## 当前能力

已实现的核心能力：

```text
桌面外置精灵助手
桌面精灵气泡对话与多表情切换
桌面精灵运行时开关（config.json5: elf.enabled）
后端精灵事件中心
Tauri 桌面精灵壳
打开 AiMemo 主页面
结构化选项卡确认（request_user_input）
桌面精灵长按语音输入、ASR 转文本
精灵气泡 TTS 播放与语音对话模式
语音工坊：声线列表、试听、文字声音设计、默认声线切换
笔记创建与本地 SQLite 存储
笔记标题 / 摘要 / 标签自动整理
笔记 chunk 分片与 embedding 入库
基于 sqlite-vec 的本地向量检索
Memory Chat Graph 对话
流式回答输出
片段追问 / 局部追问
消息分段展示、工具调用链展示、对话中断和消息分支删除
上下文金字塔构建
长期记忆提取、启用、停用和编辑
LangGraph checkpoint 持久化
本地 job 队列与启动恢复
任务 graph 可视化
Graph 调试工作台 checkpoint history / state diff
Local Operator 本地读写文件、短时命令执行和后台服务任务
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
React Router
lucide-react
```

## 项目结构

```text
backend/
  app/
    agent/       LangGraph graph、模型、streaming、上下文构建
    api/         FastAPI 路由
    jobs/        本地任务队列、worker、reconciler
    local_operator/ 本地文件 / 命令工具、权限策略和审计
    models/      SQLModel 数据模型
    rag/         chunking、hash、vector store、search
    services/    业务服务层
  tests/         后端测试

frontend/
  src/
    app/         AppShell、模块路由
    pages/       memo/chat/workshop 页面
    features/
      chat/      对话窗口、stream、graph 调试
      elf/       精灵助手
      jobs/      精灵工坊和任务可视化
      memories/  长期记忆管理
      notes/     笔记列表、编辑器、详情
      voice/     语音工坊和声线管理
    shared/      通用 UI 和 QueryClient

desktop/
  src-tauri/     Tauri 桌面精灵壳
  src/           精灵窗口、气泡对话、拖拽和菜单
  public/elf/    精灵 PNG 表情资源

docs/
  agent/         Agent / graph 设计文档
  api/           API 文档
  architecture/  架构与流程图
  backend/       后端模块说明
  frontend/      前端模块与体验优化记录
```

## 快速开始

### 1. 克隆项目

```powershell
git clone https://github.com/LiuJiaxuan1024/AiMemo.git
cd AiMemo
```

### 2. 准备环境变量

复制示例配置，并填入自己的阿里百炼 API Key。聊天、embedding、远程 ASR/TTS/Voice Design 默认复用同一套 Key：

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

仓库根目录的 `config.json5` 保存可提交的项目级开关。常用项：

```json5
{
  "elf": {
    // false 时不加载 Web/桌面精灵；精灵工坊页面仍可访问。
    "enabled": true,
  },
  "voice": {
    "enabled": true,
    "asr_provider": "aliyun_dashscope",
    "tts_provider": "aliyun_dashscope",
    "voice_design_provider": "aliyun_dashscope",
  },
}
```

### 3. 一键开发启动

开发时推荐一键启动前后端。脚本会分别启动：

```text
后端 FastAPI: http://127.0.0.1:8000
前端 Vite:    http://127.0.0.1:5173/app/
桌面精灵:      Memo Elf Tauri 窗口
```

一键脚本还会检查后端托管入口 `http://127.0.0.1:8000/app` 使用的
`frontend/dist` 是否缺失或过期；如果前端源码比上次构建更新，会自动执行
`npm run build`。Windows 后端开发启动默认启用 uvicorn reload，因此 Python
源码修改后会自动重载进程。

Windows PowerShell：

```powershell
.\scripts\start-dev.ps1
```

Linux / macOS：

```bash
chmod +x scripts/start-dev.sh scripts/start-backend.sh scripts/start-frontend.sh
./scripts/start-dev.sh
```

依赖已经安装好时：

```powershell
.\scripts\start-dev.ps1 -SkipInstall
```

```bash
./scripts/start-dev.sh --skip-install
```

后端脚本会确保 `backend/.venv` 使用 Python 3.12。如果已有虚拟环境不是 Python 3.12，会自动重建；Windows 在找不到 Python 3.12 时会尝试通过 `winget` 安装。也可以用 `AIMEMO_PYTHON` 指向指定的 Python 3.12 解释器。

前端 graph 图依赖 `mermaid`。启动脚本会在 `node_modules` 缺失或 `mermaid` 缺失时自动执行 `npm install`；如果强制跳过安装后仍报缺包，请在 `frontend/` 下手动执行 `npm install`。

`npm` 是硬要求；如果没有 Node.js / npm，脚本会直接提示安装 Node.js 20+。Rust / Cargo 只影响桌面 Memo Elf，没装 Rust 时一键脚本会跳过桌面精灵，后端和 Web 仍会继续启动。

如果只想调试 Web，不启动桌面精灵：

```powershell
.\scripts\start-dev.ps1 -NoDesktop
```

```bash
./scripts/start-dev.sh --no-desktop
```

停止所有开发进程：

```powershell
.\scripts\stop-dev.ps1
```

```bash
./scripts/stop-dev.sh
```

`stop-dev` 会停止后端、Vite 前端、Tauri desktop webview 和残留的 Memo Elf 桌面进程，避免重复启动后出现多个精灵。

### 4. 单独启动后端

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

AiMemo 统一入口：

```text
http://127.0.0.1:8000/app
```

API 文档：

```text
http://127.0.0.1:8000/docs
```

### 5. 构建前端

后端会托管 `frontend/dist`，所以正常使用前需要先构建前端：

```powershell
cd frontend
npm install
npm run build
cd ..
```

构建完成后访问：

```text
http://127.0.0.1:8000/app
```

如果你正在开发前端，需要热更新，再另开一个终端窗口启动 Vite。

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
http://127.0.0.1:5173/app/
```

语音工坊：

```text
http://127.0.0.1:8000/app/workshop/voice
http://127.0.0.1:5173/app/workshop/voice
```

### 6. 验证

启动后，桌面精灵会出现。你可以先打开 AiMemo 创建一条笔记。如果后端和模型配置正常，稍等片刻后会看到：

```text
桌面精灵可以显示气泡反馈
笔记摘要 / 标签生成
笔记进入向量化任务
进入工坊查看后台任务状态
进入语音工坊创建 / 试听 / 激活声线
精灵或对话窗口可以基于记忆回答问题
```

更多启动细节和常见问题见：[安装与启动](./docs/setup.md)。

## 手动启动

如果不使用脚本，也可以按下面方式手动启动。

### 后端

必须使用 Python 3.12。`backend/pyproject.toml` 和项目启动脚本都会硬卡 Python 3.12，避免其他版本带来的依赖兼容问题。

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

AiMemo 统一入口：

```text
http://127.0.0.1:8000/app
```

### 前端开发服务

```powershell
cd frontend
npm install
npm run dev -- --host 127.0.0.1
```

前端开发服务地址：

```text
http://127.0.0.1:5173/app/
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

- [文档目录](./docs/README.md)
- [安装与启动](./docs/setup.md)
- [架构概览](./docs/architecture/overview.md)
- [流程图](./docs/architecture/flows.md)
- [Memo Elf 桌面化架构](./docs/desktop/memo-elf-desktop-architecture.md)
- [外置精灵聊天](./docs/desktop/elf-external-chat.md)
- [阿里云远程语音能力接入设计](./docs/desktop/aliyun-voice-provider.md)
- [语音工坊第一版设计](./docs/desktop/voice-workshop-design.md)
- [Memory Chat Graph](./docs/agent/memory-chat-graph.md)
- [Memory Chat Graph 设计草案](./docs/agent/memory-chat-graph-design.md)
- [Local Operator Agent](./docs/agent/local-operator-agent.md)
- [前后台任务边界](./docs/agent/background-vs-foreground.md)
- [本地任务系统](./docs/backend/jobs.md)
- [向量存储](./docs/backend/vector-storage.md)
- [精灵助手](./docs/frontend/elf-assistant.md)
- [Chat Window](./docs/frontend/chat-window.md)
- [精灵图片生成提示词模板](./docs/frontend/elf-image-prompts.md)
- [前端体验优化报告](./docs/frontend/ui-optimization-report.md)

## 注意事项

```text
当前仍是早期开发项目，接口和数据结构可能频繁变化。
本地数据库、checkpoint、日志、虚拟环境和 node_modules 不会提交到仓库。
当前精灵使用本地 PNG 表情资源，后续如接入 Live2D，需要确认模型资源版权和分发许可。
```

## License

MIT
