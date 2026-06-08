# 安装与启动

这份文档面向第一次 clone AiMemo 的用户，目标是尽量用最少步骤把本地 Web 应用跑起来。

## 环境要求

推荐环境：

```text
Python 3.12
Node.js 20 或更高版本
npm
Git
Rust / Cargo（仅桌面 Memo Elf 需要）
```

说明：

```text
后端运行环境必须使用 Python 3.12，`backend/pyproject.toml` 和启动脚本都会硬卡 3.12，避免 LangGraph / LangChain / sqlite-vec 等依赖在其他 Python 版本上出现兼容问题。
Node.js / npm 是 Web 前端和桌面壳启动必需项。Rust / Cargo 只影响桌面 Memo Elf；没装 Rust 时一键脚本会跳过桌面精灵，后端和 Web 仍可启动。
```

## 1. 克隆项目

```powershell
git clone https://github.com/LiuJiaxuan1024/AiMemo.git
cd AiMemo
```

## 2. 配置 API Key

复制环境变量模板：

Windows PowerShell：

```powershell
Copy-Item .env.example .env
```

Linux / macOS：

```bash
cp .env.example .env
```

编辑 `.env`，填入阿里百炼 DashScope API Key：

```text
DASHSCOPE_API_KEY=你的百炼 API Key
```

当前默认模型配置：

```text
DASHSCOPE_BASE_URL=https://dashscope.aliyuncs.com/compatible-mode/v1
DASHSCOPE_EMBEDDING_MODEL=text-embedding-v4
EMBEDDING_DIMENSIONS=1024
```

Local Operator 默认授权仓库根目录和当前用户 Home 目录，用于受控文件读取、写入和命令工作目录。需要追加更多目录时，
可以在 `.env` 中配置：

```text
LOCAL_OPERATOR_WORKSPACE_ROOTS=E:\Ai记;D:\资料;~/Documents
```

多个路径可用分号或逗号分隔。敏感文件、数据库文件和密钥文件仍会被默认拦截。

`.env` 放在仓库根目录即可。后端配置会同时兼容从仓库根目录或 `backend/` 目录启动。

## 3. 一键开发启动

开发时推荐用一键脚本同时启动后端、Vite 前端和桌面外置精灵。

Windows 可以先注册全局 `aimemo` 命令。当前注册脚本只写入用户级 wrapper 和 PATH，不安装 Python / Node / Rust：

```powershell
.\scripts\register-aimemo.ps1
```

也可以通过统一入口调用同一套注册逻辑：

```powershell
.\scripts\aimemo.ps1 register
```

Linux / macOS：

```bash
chmod +x scripts/*.sh
./scripts/register-aimemo.sh
```

也可以通过统一入口调用同一套注册逻辑：

```bash
./scripts/aimemo.sh register
```

注册后，当前终端或新打开的终端可以直接运行：

```powershell
aimemo doctor
aimemo start
aimemo stop
```

如果只想查看会执行哪些动作：

```powershell
.\scripts\register-aimemo.ps1 -DryRun
```

Linux / macOS：

```bash
./scripts/register-aimemo.sh --dry-run
```

Windows PowerShell：

```powershell
.\scripts\aimemo.ps1 start
```

Linux / macOS：

```bash
chmod +x scripts/*.sh
./scripts/aimemo.sh start
```

启动后访问：

```text
后端 API:     http://127.0.0.1:8000
前端开发页:   http://127.0.0.1:5173/app/
API 文档:     http://127.0.0.1:8000/docs
桌面精灵:     Memo Elf Tauri 透明窗口
```

Windows 默认只保留当前启动终端和桌面精灵窗口，不再额外弹出后端、前端、桌面三个 PowerShell 服务窗口。服务 stdout/stderr 会写入：

```text
data/dev_logs/
```

如果需要调试启动过程，可以恢复旧的多窗口模式：

```powershell
.\scripts\aimemo.ps1 start -SeparateWindows
```

语音工坊入口：

```text
http://127.0.0.1:8000/app/workshop/voice
```

一键脚本会在启动前刷新后端托管入口使用的前端产物：
`http://127.0.0.1:8000/app` 读取的是 `frontend/dist`，不是 Vite 的
`5173` 开发产物。脚本会检查 `dist/index.html` 是否缺失或比前端源码旧，
必要时自动执行 `npm run build`。Windows 后端开发启动默认启用 uvicorn
reload，便于 Python 代码改动后自动生效。

脚本还会做启动前检查：

```text
npm 不存在
  -> 直接提示安装 Node.js 20+。

frontend/node_modules 或 mermaid 缺失
  -> 自动执行 npm install，避免 graph 图无法渲染。

backend/.venv 不是 Python 3.12
  -> 自动重建。

Rust/Cargo 不存在
  -> 跳过桌面 Memo Elf，只启动后端和 Web。
```

依赖已经安装好时，可以跳过安装：

Windows PowerShell：

```powershell
.\scripts\aimemo.ps1 start -SkipInstall
```

Linux / macOS：

```bash
./scripts/aimemo.sh start --skip-install
```

如果只想调试 Web，不启动桌面精灵：

Windows PowerShell：

```powershell
.\scripts\aimemo.ps1 start -NoDesktop
```

Linux / macOS：

```bash
./scripts/aimemo.sh start --no-desktop
```

启动前也可以先运行环境诊断。第一版 doctor 只检查状态，不会安装或修改依赖：

Windows PowerShell：

```powershell
.\scripts\aimemo.ps1 doctor
```

Linux / macOS：

```bash
chmod +x scripts/*.sh
./scripts/aimemo.sh doctor
```

`start-dev` 默认会先跑一次非阻塞快速诊断；即使诊断发现问题，仍会继续沿用当前兼容启动逻辑。若想跳过：

Windows PowerShell：

```powershell
.\scripts\aimemo.ps1 start -SkipDoctor
```

Linux / macOS：

```bash
./scripts/aimemo.sh start --skip-doctor
```

停止所有开发进程：

Windows PowerShell：

```powershell
.\scripts\aimemo.ps1 stop
```

Linux / macOS：

```bash
./scripts/aimemo.sh stop
```

`stop-dev` 会停止后端、Vite 前端、Tauri desktop webview 和残留的 Memo Elf 桌面进程，避免重复启动后出现多个精灵。

### Python 虚拟环境策略

后端必须使用 Python 3.12。启动脚本会检查 `backend/.venv`：

```text
backend/.venv 不存在
  -> 用 Python 3.12 创建。

backend/.venv 存在但不是 Python 3.12
  -> 删除旧虚拟环境并用 Python 3.12 重建。
```

Windows 下如果没有 Python 3.12，脚本会尝试用 `winget install Python.Python.3.12` 安装。
如果机器上已有兼容 Python 但不在 PATH，可以显式指定：

Windows PowerShell：

```powershell
$env:AIMEMO_PYTHON="C:\Users\you\AppData\Local\Programs\Python\Python312\python.exe"
.\scripts\start-dev.ps1
```

Linux / macOS：

```bash
export AIMEMO_PYTHON=/opt/homebrew/opt/python@3.12/bin/python3.12
./scripts/start-dev.sh
```

Linux / macOS 下脚本不会静默安装系统 Python，而是提示用户安装。示例：

```bash
# Ubuntu / Debian
sudo apt install python3.12 python3.12-venv

# Fedora
sudo dnf install python3.12

# macOS
brew install python@3.12
```

## 4. 单独启动后端

推荐使用脚本：

Windows PowerShell：

```powershell
.\scripts\start-backend.ps1
```

Linux / macOS：

```bash
chmod +x scripts/start-backend.sh scripts/start-frontend.sh
./scripts/start-backend.sh
```

脚本会做这些事：

```text
创建 backend/.venv
安装后端依赖
启动 FastAPI 服务
```

后端地址：

```text
http://127.0.0.1:8000
```

API 文档：

```text
http://127.0.0.1:8000/docs
```

后续依赖已经装好时，可以跳过安装：

Windows PowerShell：

```powershell
.\scripts\start-backend.ps1 -SkipInstall
```

Linux / macOS：

```bash
./scripts/start-backend.sh --skip-install
```

## 5. 构建并访问前端

后端网关会托管 `frontend/dist`，正常使用不需要单独启动 Vite。先构建前端：

Windows PowerShell / Linux / macOS：

```powershell
cd frontend
npm install
npm run build
cd ..
```

构建完成后，访问后端统一入口：

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

脚本会安装前端依赖并启动 Vite dev server。

```text
http://127.0.0.1:5173/app/
```

主要前端路由：

```text
/app/memo
/app/chat
/app/workshop/jobs
/app/workshop/memories
/app/workshop/voice
```

后续依赖已经装好时，可以跳过安装：

Windows PowerShell：

```powershell
.\scripts\start-frontend.ps1 -SkipInstall
```

Linux / macOS：

```bash
./scripts/start-frontend.sh --skip-install
```

Graph 图渲染依赖前端包 `mermaid`。启动脚本会在检测到 `node_modules` 缺失或 `mermaid` 缺失时自动执行 `npm install`。
如果你强制跳过安装后仍看到缺失 `mermaid` 的报错，请在 `frontend/` 下执行：

```bash
npm install
```

## 6. 手动启动

后端：

Windows PowerShell：

```powershell
cd backend
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -U pip
python -m pip install -e ".[dev]"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

Linux / macOS：

```bash
cd backend
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -U pip
python -m pip install -e ".[dev]"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

前端生产构建：

```powershell
cd frontend
npm install
npm run build
```

Linux / macOS 同样使用：

```bash
cd frontend
npm install
npm run build
```

如果你正在开发前端，需要热更新，可以使用 Vite dev server：

```powershell
cd frontend
npm run dev -- --host 127.0.0.1
```

开发服务地址：

```text
http://127.0.0.1:5173/app/
```

## 6. 验证

后端健康检查：

Windows PowerShell：

```powershell
Invoke-WebRequest http://127.0.0.1:8000/api/health -UseBasicParsing
```

Linux / macOS：

```bash
curl http://127.0.0.1:8000/api/health
```

预期返回：

```json
{"status":"ok"}
```

前端验证：

```text
打开 http://127.0.0.1:8000/app
创建一条笔记
进入 /app/workshop/jobs 查看后台任务
进入 /app/chat 询问与笔记相关的问题
```

## 常见问题

### PowerShell 不允许执行脚本

如果遇到执行策略限制，可以只对当前用户放开：

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

也可以直接手动启动，不使用脚本。

### Linux 脚本没有执行权限

执行：

```bash
chmod +x scripts/start-backend.sh scripts/start-frontend.sh
```

然后重新运行脚本。

### 后端提示缺少 DASHSCOPE_API_KEY

检查仓库根目录 `.env` 是否存在，并确认：

```text
DASHSCOPE_API_KEY=你的百炼 API Key
```

如果你在系统环境变量里配置了同名变量，系统环境变量会优先生效。

### 8000 端口被占用

查看占用进程：

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen
```

停止旧后端进程后重新启动。

### 前端请求后端失败

确认后端健康检查正常：

```text
http://127.0.0.1:8000/api/health
```

正常入口是 `http://127.0.0.1:8000/app`，前端会使用同源 `/api/*`。
如果你使用 Vite dev server，请确认 `VITE_API_BASE_URL` 指向后端，或后端 CORS 配置包含开发服务 origin。

### 精灵没有显示或连接失败

当前推荐使用桌面外置精灵。确认后端健康检查正常，并确认 `config.json5` 中：

```json5
"elf": {
  "enabled": true,
}
```

桌面精灵启动后会读取：

```text
GET /api/config/runtime
```

如果该接口暂时不可用，桌面精灵会等待并重试；只有接口明确返回 `elf.enabled=false`
时才会保持隐藏。

确认桌面端监听：

```text
http://127.0.0.1:1420
```

如果只启动后端和 Vite 前端，不会显示外置精灵。外置精灵由 `desktop/`
里的 Tauri 进程提供。推荐直接使用：

```powershell
.\scripts\start-dev.ps1
```

浏览器内精灵也受 `config.json5` 的 `elf.enabled` 控制。`false` 时 Web 不挂载精灵，但仍保留精灵工坊侧边入口。

旧版 `VITE_ENABLE_WEB_ELF` 只作为历史开发开关保留，不再绕过运行时配置。

### 语音工坊或语音对话不可用

确认 `.env` 中配置了：

```text
DASHSCOPE_API_KEY=你的百炼 API Key
```

确认 `config.json5` 中 `voice.enabled=true`，并进入：

```text
/app/workshop/voice
```

语音工坊使用阿里百炼远程 ASR、TTS 和 Voice Design，不需要安装 SenseVoice、VoxCPM2、CUDA 或本地 wrapper。
